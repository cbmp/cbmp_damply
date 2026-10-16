import datetime
import getpass
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import click

from damply import __version__ as damply_version
from damply.logging_config import logger
from damply.project import DirectoryAudit

from .utils import (
	InvalidProjectGroupError,
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

			return {
				'status': 'error',
				'error': f'{etype}: {emsg}',
				'traceback': stack,
			}

	# Audit subdirectories
	for subdir in sorted(directory.iterdir()):
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
def full_audit(project_group: str, compute_details: bool, job_time: str, max_jobs: int,) -> None:
	"""Run a full audit for the specified project group.

	This will essentially, submit a bunch of sbatch jobs to the cluster
	for all the directories in the project group.
	"""
	run_full_audit(project_group, compute_details=compute_details, job_time=job_time, max_jobs=max_jobs,)


# Setup some assumptions about project groups and directories


def run_full_audit(project_group: str,
	compute_details: bool = False,
	job_time: str = '01:00:00',
	max_jobs: int = 2000,
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
	logger.debug(
		f'Found {len(directories)} directories in project group {project_group}'
	)

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
			'4G',
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


def get_directories(root: Path) -> list[Path]:
	# Audit all directories within the project group
	dirs = [
		d
		for d in root.rglob('*')
		if d.is_dir()
		and not d.is_symlink()
		and d.relative_to(root).parts[:2] != ('admin', 'audit')
	]
	dirs.sort(key=lambda x: str(x))
	logger.info(f'Found {len(dirs)} directories in {root}')
	return dirs
