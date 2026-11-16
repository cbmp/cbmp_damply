import csv
import json
from pathlib import Path

import click
from bytesize import ByteSize

from damply.cli.audit.collect_audits import get_latest_result_directory
from damply.logging_config import logger

CSV_COLUMNS = [
	'path',
	'parent_path',
	'parent',
	'depth',
	'name',
	'size',
	'size_gb',
	'file_count',
	'owner',
	'group',
	'full_name',
	'permissions',
	'audit_status',
	'last_modified',
	'last_changed',
	'readme_path',
	'meta.OWNER',
	'meta.DESC',
	'meta.EMAIL',
	'meta.DATE',
	'file_types',
]
METADATA_FIELDS = ('OWNER', 'DESC', 'EMAIL', 'DATE')


@click.command('collect-batch-audits')
@click.argument('project_group')
@click.option(
	'--run-id',
	help='Collect a specific batch-audit run; defaults to the latest run for the latest date.',
)
def collect_batch_audits(project_group: str, run_id: str | None) -> None:
	"""Combine batch-audit JSONL results into a CSV summary."""
	latest_results_dir = get_latest_result_directory(project_group)
	batch_results_dir = latest_results_dir / 'batch-audit'
	if not batch_results_dir.is_dir():
		raise click.ClickException(f'No batch-audit results found in {batch_results_dir}')

	if run_id is None:
		run_dirs = sorted(
			(path for path in batch_results_dir.iterdir() if path.is_dir()),
			key=lambda path: path.name,
		)
		if not run_dirs:
			raise click.ClickException(
				f'No batch-audit runs found in {batch_results_dir}'
			)
		run_dir = run_dirs[-1]
	else:
		run_dir = batch_results_dir / run_id
		if not run_dir.is_dir():
			raise click.ClickException(f'Batch-audit run does not exist: {run_dir}')

	output_path = write_batch_audit_summary(project_group, run_dir)
	click.echo(output_path.as_posix())


def write_batch_audit_summary(project_group: str, run_dir: Path) -> Path:
	"""Stream batch JSONL records directly to the summary CSV."""
	batch_files = sorted(run_dir.glob('batch-*.jsonl'))
	if not batch_files:
		raise click.ClickException(f'No batch result files found in {run_dir}')

	output_path = run_dir / f'{project_group}-batch-audit_summary.csv'
	written = 0
	skipped = 0

	with output_path.open('w', newline='', encoding='utf-8') as output:
		writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
		writer.writeheader()

		for batch_file in batch_files:
			if not batch_file.with_suffix('.complete').is_file():
				logger.warning(
					f'Batch result may be incomplete (no completion marker): {batch_file}'
				)

			with batch_file.open(encoding='utf-8') as batch:
				for line_number, line in enumerate(batch, start=1):
					try:
						entry = json.loads(line)
						source = entry.get('source_directory', {})
						data = source.get('data', {})
						if not data.get('path'):
							raise ValueError('Result has no source directory path.')
						writer.writerow(
							_record_to_csv_row(data, source.get('status', 'error'))
						)
						written += 1
					except (json.JSONDecodeError, TypeError, ValueError) as e:
						logger.error(
							f'Unable to process {batch_file}:{line_number}: {e}'
						)
						skipped += 1

	logger.info(
		f'Wrote {written} directory records to {output_path}; skipped {skipped}.'
	)
	return output_path


def _record_to_csv_row(data: dict, status: str) -> dict[str, object]:
	path = Path(data['path'])
	project_root = Path(*path.parts[:4])
	size = data.get('size')
	try:
		size_gb = f'{ByteSize(size).GB:.5f}' if size is not None else None
	except (TypeError, ValueError):
		size_gb = None

	row = {
		'path': path.as_posix(),
		'parent_path': path.parent.as_posix(),
		'parent': path.parent.relative_to(project_root).as_posix(),
		'depth': len(path.relative_to(path.anchor).parts),
		'name': path.name,
		'size': size,
		'size_gb': size_gb,
		'file_count': data.get('file_count'),
		'owner': data.get('owner', 'Unknown'),
		'group': data.get('group', 'Unknown'),
		'full_name': data.get('full_name', 'Unknown'),
		'permissions': data.get('permissions', 'Unknown'),
		'audit_status': status,
		'last_modified': data.get('last_modified'),
		'last_changed': data.get('last_changed'),
		'readme_path': data.get('readme_path'),
		'file_types': data.get('file_types'),
	}
	metadata = data.get('metadata') or {}
	for field in METADATA_FIELDS:
		row[f'meta.{field}'] = metadata.get(field)
	return row
