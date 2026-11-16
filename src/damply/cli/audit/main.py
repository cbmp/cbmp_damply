import datetime
import getpass
import json
import os
import shlex
import stat
import subprocess
import sys
import time
import traceback
from collections.abc import Iterator
from pathlib import Path

import click
from bytesize import ByteSize

from damply import __version__ as damply_version
from damply.logging_config import logger
from damply.project import DirectoryAudit

from .utils import (
	OutsideProjectPathError,
	get_parents_path,
	get_project_group,
)


@click.option(
	'--force-compute-details',
	'-f',
	'compute_details',
	help='Force the computation of details for the directory and subdirectories regardless of cache.',
	is_flag=True,
	default=False,
	show_default=True,
)
@click.argument(
	'directory', type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.command('audit')
def audit(directory: Path, compute_details: bool) -> None:
	"""Audit all subdirectories and aggregate damply output into a single JSON.

	Unlike the 'damply proejct' command, this will by default, try to compute
	details, using cache if it exists. If you want to force the computation
	of details for the directory and subdirectories, use the --force-compute-details
	flag. This will ignore any cached results and recompute everything.

	"""

	# resolve the directory to scan
	directory = directory.expanduser().resolve().absolute()

	logger.info(f'Starting audit for directory: {directory}')

	now = datetime.datetime.now()
	timestamp = now.strftime('%Y-%m-%dT%H-%M-%S')
	date = now.strftime('%Y-%m-%d')

	try:
		project_group = get_project_group(directory)
		relative_path = get_parents_path(project_group, directory)
	except (InvalidProjectGroupError, OutsideProjectPathError) as e:
		click.echo(f'Error: {e}', err=True)
		sys.exit(1)

	results_dir = Path(
		f'/cluster/projects/{project_group}/admin/audit/results/{date}/{relative_path}'
	)
	results_dir / 'audit.json'

	logger.debug(
		f'Creating results directory: {results_dir} '
		f'for project group: {project_group}, relative path: {relative_path}'
	)
	results_dir.mkdir(parents=True, exist_ok=True)
	output_file = results_dir / 'audit.json'

	# Initialize combined results
	results = {
		'audit_date': timestamp,
		'source_directory': {},
		'directories': {},
		'damply_version': damply_version,
	}

	def safe_audit(path: Path) -> dict:
		audit_obj = None
		try:
			logger.info(f'Auditing: {path}')
			audit_obj = DirectoryAudit.from_path(path)
			audit_obj.compute_details(show_progress=True, force=compute_details)
			return {
				'status': 'ok',
				'data': audit_obj.to_dict(),
			}
		except Exception as e:
			etype = type(e).__name__
			emsg = str(e)
			stack = traceback.format_exc(limit=5)  # limit frames if needed
			logger.exception(f'Failed audit: {path}')

			result = {
				'status': 'error',
				'error': f'{etype}: {emsg}',
				'traceback': stack,
			}
			result['data'] = (
				audit_obj.to_dict() if audit_obj is not None else {'path': str(path)}
			)
			return result

	# Audit subdirectories
	try:
		subdirectories = sorted(directory.iterdir())
	except OSError as e:
		logger.warning(f'Unable to list subdirectories of {directory}: {e}')
		subdirectories = []

	for subdir in subdirectories:
		if subdir.is_dir():
			results['directories'][subdir.name] = safe_audit(subdir)

	# count number of errors
	logger.info(f'Total directories audited: {len(results["directories"])}')
	error_count = sum(
		1 for result in results['directories'].values() if result['status'] == 'error'
	)
	if error_count > 0:
		logger.error(f'Total errors encountered: {error_count}')

	# Audit the main source directory
	results['source_directory'] = safe_audit(directory)

	# Save combined results
	output_file.write_text(json.dumps(results, default=str, indent=2))
	logger.info(f'Audit results saved to: {output_file}')


@click.command('full-audit')
@click.argument('project_group')
@click.option(
	'--force-compute-details',
	'-f',
	'compute_details',
	help='Force the computation of details for the directory and subdirectories regardless of cache.',
	is_flag=True,
	default=False,
	show_default=True,
)
@click.option(
	'--sbatch-time',
	'-t',
	'job_time',
	help='Time limit for each sbatch job (default: 01:00:00)',
	default='01:00:00',
	show_default=True,
)
@click.option(
	'--max-jobs',
	'max_jobs',
	type=click.IntRange(min=1),
	default=2000,
	show_default=True,
	help='Sets maximum number of slurm jobs to submit to cluster to avoid failure due to QOS violations when running audit on many directories',
)
@click.option(
	'--sbatch-memory',
	'-m',
	'job_memory',
	help='Memory limit for each sbatch job (default: 4G)',
	default='4G',
	show_default=True,
)
def full_audit(project_group: str, compute_details: bool, job_time: str, max_jobs: int, job_memory:str) -> None:
	"""Run a full audit for the specified project group.

	This will essentially, submit a bunch of sbatch jobs to the cluster
	for all the directories in the project group.
	"""
	run_full_audit(project_group, compute_details=compute_details, job_time=job_time, max_jobs=max_jobs, job_memory=job_memory)


@click.command('batch-audit')
@click.argument('project_group')
@click.option(
	'--batch-size',
	type=click.IntRange(min=1),
	default=1000,
	show_default=True,
	help='Number of non-overlapping directory trees assigned to each Slurm job.',
)
@click.option(
	'--fanout-threshold',
	type=click.IntRange(min=1),
	default=1000,
	show_default=True,
	help='Split a directory into child-tree batches at this many immediate subdirectories.',
)
@click.option(
	'--sbatch-time',
	'-t',
	'job_time',
	default='04:00:00',
	show_default=True,
	help='Time limit for each batch job.',
)
@click.option(
	'--max-jobs',
	type=click.IntRange(min=1),
	default=2000,
	show_default=True,
	help='Maximum number of pending or running Slurm jobs.',
)
@click.option(
	'--sbatch-memory',
	'-m',
	'job_memory',
	default='4G',
	show_default=True,
	help='Memory limit for each batch job.',
)
def batch_audit(
	project_group: str,
	batch_size: int,
	fanout_threshold: int,
	job_time: str,
	max_jobs: int,
	job_memory: str,
) -> None:
	"""Submit batched, recursive audits for a project group."""
	root = Path(f'/cluster/projects/{project_group}').resolve()
	if not root.is_dir():
		raise OutsideProjectPathError(f'Project group directory {root} is unavailable.')

	recursive_roots, exact_paths = plan_batch_audit(root, fanout_threshold)
	if not recursive_roots and not exact_paths:
		logger.warning(f'No directories found in {root}')
		return
	logger.info(
		f'Planned {len(recursive_roots)} recursive trees and '
		f'{len(exact_paths)} exact parent audits'
	)

	now = datetime.datetime.now()
	date = now.strftime('%Y-%m-%d')
	run_id = now.strftime('%H%M%S%f')
	audit_root = root / 'admin' / 'audit'
	manifest_dir = audit_root / 'jobs' / date / run_id
	log_dir = audit_root / 'logs' / date / 'batch-audit' / run_id
	batch_results_dir = audit_root / 'results' / date / 'batch-audit' / run_id
	manifest_dir.mkdir(parents=True, exist_ok=True)
	log_dir.mkdir(parents=True, exist_ok=True)
	batch_results_dir.mkdir(parents=True, exist_ok=True)

	job_batches = [
		(recursive_roots[start : start + batch_size], [])
		for start in range(0, len(recursive_roots), batch_size)
	]
	job_batches.extend(
		([], exact_paths[start : start + batch_size])
		for start in range(0, len(exact_paths), batch_size)
	)

	for batch_number, (batch_roots, batch_exact_paths) in enumerate(job_batches):
		manifest = manifest_dir / f'batch-{batch_number:05d}.json'
		output_file = batch_results_dir / f'batch-{batch_number:05d}.jsonl'
		manifest.write_text(
			json.dumps(
				{
					'project_root': str(root),
					'audit_date': now.strftime('%Y-%m-%dT%H-%M-%S'),
					'batch_run_id': run_id,
					'roots': [str(path) for path in batch_roots],
					'exact_paths': [str(path) for path in batch_exact_paths],
					'output_file': str(output_file),
					'complete_file': str(output_file.with_suffix('.complete')),
				},
				indent=2,
			)
		)

		wait_for_job_slot(max_jobs)
		job_name = f'batch_audit_{batch_number:05d}'
		cmd = [
			'sbatch',
			'--job-name',
			job_name,
			'--output',
			str(log_dir / f'{job_name}_%j.out'),
			'--time',
			job_time,
			'--mem',
			job_memory,
			'--wrap',
			'DAMPLY_LOG_LEVEL=DEBUG damply _batch-audit-worker '
			f'{shlex.quote(str(manifest))}',
		]
		try:
			result = subprocess.run(cmd, check=True, capture_output=True, text=True)
			logger.info(f'Submitted {job_name}: {result.stdout.strip()}')
		except subprocess.CalledProcessError as e:
			logger.error(f'Failed to submit {job_name}: {e.stderr.strip()}')


@click.command('_batch-audit-worker', hidden=True)
@click.argument(
	'manifest',
	type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def batch_audit_worker(manifest: Path) -> None:
	"""Run one batch-audit job from its manifest."""
	run_batch_audit_worker(manifest)


def run_batch_audit_worker(manifest: Path) -> None:
	"""Write one JSON Lines result file for this batch."""
	job = json.loads(manifest.read_text())
	output_file = Path(job['output_file'])
	complete_file = Path(job['complete_file'])
	output_file.parent.mkdir(parents=True, exist_ok=True)
	complete_file.unlink(missing_ok=True)

	with output_file.open('w', encoding='utf-8') as output:
		for record in _iter_batch_audit_records(job):
			output.write(json.dumps(record, default=str))
			output.write('\n')

	complete_file.write_text('complete\n', encoding='utf-8')


def _iter_batch_audit_records(job: dict) -> Iterator[dict]:
	"""Audit each directory in a batch with a shared walk and size scan."""
	project_root = Path(job['project_root'])
	roots = [Path(path) for path in job['roots']]
	exact_paths = [Path(path) for path in job.get('exact_paths', [])]
	audit_date = job['audit_date']
	batch_run_id = job['batch_run_id']

	directories: set[Path] = set()
	children: dict[Path, list[Path]] = {}
	direct_file_counts: dict[Path, int] = {}
	direct_file_types: dict[Path, set[str]] = {}
	failed: dict[Path, str] = {}
	audit_dir = project_root / 'admin' / 'audit'

	def log_walk_error(error: OSError) -> None:
		path = Path(error.filename) if error.filename else project_root
		failed[path] = str(error)
		logger.warning(f'Unable to scan directory {path}: {error}')

	for scan_root in roots:
		directories.add(scan_root)
		for current, child_dirs, filenames in os.walk(
			scan_root, topdown=True, onerror=log_walk_error
		):
			current_path = Path(current)
			child_dirs[:] = sorted(
				name
				for name in child_dirs
				if current_path / name != audit_dir
				and not (current_path / name).is_symlink()
			)
			child_paths = [current_path / name for name in child_dirs]
			directories.update(child_paths)
			children[current_path] = child_paths

			count = 0
			suffixes: set[str] = set()
			for filename in filenames:
				file_path = current_path / filename
				try:
					if not stat.S_ISREG(file_path.stat().st_mode):
						continue
				except OSError as e:
					failed[current_path] = str(e)
					continue

				count += 1
				if (
					not file_path.name.startswith('.')
					and file_path.suffixes
					and len(file_path.suffixes) <= 3
				):
					valid_suffixes = [
						suffix
						for suffix in file_path.suffixes
						if not any(character.isdigit() for character in suffix)
					]
					suffixes.add(''.join(valid_suffixes))

			direct_file_counts[current_path] = count
			direct_file_types[current_path] = suffixes

	sizes: dict[Path, int] = {}
	if roots:
		# GNU du reports each directory total in one traversal of the batch roots.
		du_result = subprocess.run(
			['du', '-B1', '--null', '--files0-from=-'],
			input=b'\0'.join(os.fsencode(path) for path in roots) + b'\0',
			stdout=subprocess.PIPE,
			stderr=subprocess.DEVNULL,
			check=False,
		)
		for record in du_result.stdout.split(b'\0'):
			if not record:
				continue
			try:
				size_bytes, path_bytes = record.split(b'\t', 1)
				sizes[Path(os.fsdecode(path_bytes))] = int(size_bytes)
			except ValueError:
				logger.warning(f'Unable to parse du output record: {record!r}')
		if du_result.returncode:
			logger.warning(
				f'du exited with status {du_result.returncode}; using available sizes'
			)

	aggregates: dict[Path, tuple[int, set[str], bool]] = {}
	for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
		child_count = 0
		child_types: set[str] = set()
		child_failed = directory in failed
		for child in children.get(directory, []):
			count, suffixes, has_error = aggregates.get(child, (0, set(), True))
			child_count += count
			child_types.update(suffixes)
			child_failed = child_failed or has_error

		file_count = direct_file_counts.get(directory, 0) + child_count
		file_types = direct_file_types.get(directory, set()) | child_types
		has_error = child_failed or directory not in sizes
		aggregates[directory] = (file_count, file_types, has_error)

		try:
			audit_obj = DirectoryAudit.from_path(directory)
			audit_obj.size = ByteSize(sizes[directory]) if directory in sizes else None
			audit_obj.file_count = file_count
			audit_obj.file_types = sorted(file_types)
			audit_obj.last_computed = audit_date
			data = audit_obj.to_dict()
		except Exception as e:
			logger.exception(f'Unable to collect metadata for {directory}')
			data = {'path': str(directory)}
			has_error = True
			failed[directory] = str(e)
			aggregates[directory] = (file_count, file_types, True)

		status = 'error' if has_error else 'ok'
		if has_error:
			logger.warning(f'Batch audit is incomplete for {directory}')
		source_result = {
			'status': status,
			'data': data,
		}
		if directory in failed:
			source_result['error'] = failed[directory]
		entry = {
			'audit_date': audit_date,
			'batch_run_id': batch_run_id,
			'source_directory': source_result,
			'directories': {},
			'damply_version': damply_version,
		}
		yield entry

	for directory in exact_paths:
		try:
			audit_obj = DirectoryAudit.from_path(directory)
			audit_obj.compute_details(show_progress=False, force=True)
			source_result = {'status': 'ok', 'data': audit_obj.to_dict()}
		except Exception as e:
			logger.exception(f'Unable to compute exact metadata for {directory}')
			source_result = {
				'status': 'error',
				'error': f'{type(e).__name__}: {e}',
				'data': {'path': str(directory)},
			}

		entry = {
			'audit_date': audit_date,
			'batch_run_id': batch_run_id,
			'source_directory': source_result,
			'directories': {},
			'damply_version': damply_version,
		}
		yield entry


def plan_batch_audit(
	root: Path, fanout_threshold: int
) -> tuple[list[Path], list[Path]]:
	"""Partition a directory tree into non-overlapping audit roots."""
	audit_dir = root / 'admin' / 'audit'
	children: dict[Path, list[Path]] = {}

	def log_walk_error(error: OSError) -> None:
		logger.warning(f'Unable to inspect directory {error.filename}: {error}')

	for current, child_dirs, _ in os.walk(
		root, topdown=True, onerror=log_walk_error
	):
		current_path = Path(current)
		child_dirs[:] = sorted(
			name
			for name in child_dirs
			if current_path / name != audit_dir
			and not (current_path / name).is_symlink()
		)
		children[current_path] = [current_path / name for name in child_dirs]
		for child in children[current_path]:
			children.setdefault(child, [])

	contains_split: dict[Path, bool] = {}
	for directory in sorted(children, key=lambda path: len(path.parts), reverse=True):
		contains_split[directory] = (
			len(children[directory]) >= fanout_threshold
			or any(contains_split.get(child, False) for child in children[directory])
		)

	recursive_roots: list[Path] = []
	exact_paths: list[Path] = []
	pending = list(reversed(children.get(root, [])))
	while pending:
		directory = pending.pop()
		if not contains_split.get(directory, False):
			recursive_roots.append(directory)
			continue

		exact_paths.append(directory)
		pending.extend(reversed(children.get(directory, [])))

	return sorted(recursive_roots, key=str), sorted(exact_paths, key=str)


# Setup some assumptions about project groups and directories


def run_full_audit(project_group: str,
	compute_details: bool = False,
	job_time: str = '01:00:00',
	max_jobs: int = 2000,
	job_memory: str = '4G',
) -> None:
	"""Run a full audit for the specified project group.

	NOTE: THIS IS MEANT TO BE USED ON A COMPUTE NODE! NOT THE LOGIN NODE!

	This will essentially, submit a bunch of sbatch jobs to the cluster
	for all the directories in the project group.
	"""

	# Define the base directory for the project group
	root = Path(f'/cluster/projects/{project_group}')

	# do we have access to the base directory?
	if not root.exists() or not root.is_dir():
		msg = f'Base directory {root} does not exist or is not a directory.'
		msg += ' Are you sure you are running this on a compute node?'
		raise OutsideProjectPathError(msg)

	logger.info(f'Starting full audit for project group: {project_group}')
	# Get all directories in the project group
	directories = get_directories(root)

	# get relative path for the log directory
	try:
		get_parents_path(project_group, root)
	except (InvalidProjectGroupError, OutsideProjectPathError) as e:
		click.echo(f'Error: {e}', err=True)
		sys.exit(1)

	now = datetime.datetime.now()
	date = now.strftime('%Y-%m-%d')
	log_dir_base = (
		Path(f'/cluster/projects/{project_group}/admin/audit/logs') / f'{date}'
	)

	compute_details_arg = '--force-compute-details' if compute_details else ''

	for directory in directories:
		wait_for_job_slot(max_jobs)
		rel_path = get_parents_path(project_group, directory)
		job_name = rel_path.replace('/', '_').replace(' ', '_')
		log_dir = log_dir_base / rel_path
		log_dir.mkdir(parents=True, exist_ok=True)
		cmd = [
			'sbatch',
			'--job-name',
			job_name,
			'--output',
			str(log_dir / f'{job_name}_%j.out'),
			'--time',
			job_time,
			'--mem',
			job_memory,
			'--wrap',
			f'DAMPLY_LOG_LEVEL=DEBUG damply audit {directory} {compute_details_arg}',
		]
		logger.debug(f'Submitting job with command: {cmd}')

		try:
			result = subprocess.run(cmd, check=True, capture_output=True, text=True)
			logger.info(f'Job submitted successfully: {result.stdout.strip()}')
		except subprocess.CalledProcessError as e:
			logger.error(f'Failed to submit job for {directory}: {e.stderr.strip()}')
			continue


def wait_for_job_slot(max_jobs: int) -> None:
	username = getpass.getuser()
	current_job_id = os.environ.get('SLURM_JOB_ID')

	while True:
		result = subprocess.run(
			['squeue', '--noheader', '--user', username, '--format=%i'],
			check=True,
			capture_output=True,
			text=True,
		)
		job_ids = {
			job_id.strip()
			for job_id in result.stdout.splitlines()
			if job_id.strip() and job_id.strip() != current_job_id
		}
		if len(job_ids) < max_jobs:
			return

		logger.info(
			f'{len(job_ids)} of {max_jobs} allowed jobs are pending or '
			'running; checking again in 30 seconds.'
		)
		time.sleep(30)


def get_directories(root: Path) -> Iterator[Path]:
    audit_dir = root / 'admin' / 'audit'
    directory_count = 0

    def log_walk_error(error: OSError) -> None:
        logger.warning(f'Unable to scan directory {error.filename}: {error}')

    for current, child_dirs, _ in os.walk(
        root, topdown=True, onerror=log_walk_error
    ):
        current_path = Path(current)
        child_dirs[:] = sorted(
            name
            for name in child_dirs
            if current_path / name != audit_dir
            and not (current_path / name).is_symlink()
        )

        for name in child_dirs:
            directory_count += 1
            yield current_path / name

    logger.info(f'Found {directory_count} directories in {root}')
