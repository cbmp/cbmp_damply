from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import click
import pandas as pd

from damply.cli.audit.utils import OutsideProjectPathError
from damply.logging_config import logger


def get_latest_result_directory(project_group: str) -> Path:
	"""Get the results directory for the project group.

	Directory name should be in the format: YYYY-MM-DD

	If the date is older than 7 days, raise a warning that
	the results may be outdated.
	"""
	from datetime import datetime

	root = Path(f'/cluster/projects/{project_group}/admin/audit/results')

	# get all directories in the results directory
	if not root.exists() or not root.is_dir():
		msg = f'Results directory {root} does not exist or is not a directory.'
		raise OutsideProjectPathError(msg)

	subdirs = [d for d in root.iterdir() if d.is_dir()]

	# convert dates to find latest audit
	subdirs.sort(key=lambda x: x.name, reverse=True)

	if not subdirs:
		msg = f'No results directories found for {project_group}'
		raise OutsideProjectPathError(msg)

	latest = subdirs[0]
	logger.info(
		f'Latest results directory for {project_group}: {latest} out of {len(subdirs)} found'
	)
	# check if the latest directory is older than 7 days
	latest_date = datetime.strptime(latest.name, '%Y-%m-%d')
	if (datetime.now() - latest_date).days > 7:
		logger.warning(
			f'The latest results directory {latest} is older than 7 days. '
			'Results may be outdated. Consider re-running the full-audit.'
		)

	return latest


@dataclass
class DirectoryNode:
	path: str
	parent_path: str
	parent: str
	name: str
	size: int | None
	file_count: int | None
	file_types: list[str] | None
	owner: str
	group: str
	full_name: str
	permissions: str
	last_modified: str | None
	last_changed: str | None
	# last_computed: str
	readme_path: str | None
	metadata: dict[str, str]
	audit_status: str = 'ok'
	depth: int = field(default=0, init=False)
	children: list[DirectoryNode] = field(default_factory=list)

	def __post_init__(self) -> None:
		"""Calculate the depth from the root directory."""
		root = Path(self.path).anchor
		self.depth = len(Path(self.path).relative_to(root).parts)

	@classmethod
	def from_dict(cls, data: dict, status: str = 'ok') -> DirectoryNode:
		"""Create a DirectoryNode from a dictionary."""
		# parent should be calculated as relative to after the 3rd slash
		try:
			p = Path(data['path'])
			return cls(
				path=p.as_posix(),
				parent_path=p.parent.as_posix(),
				parent=p.parent.relative_to(Path(*p.parts[0:4])).as_posix(),
				name=p.name,
				size=data.get('size'),
				file_count=data.get('file_count'),
				file_types=data.get('file_types'),
				owner=data.get('owner', 'Unknown'),
				group=data.get('group', 'Unknown'),
				full_name=data.get('full_name', 'Unknown'),
				permissions=data.get('permissions', 'Unknown'),
				last_modified=data.get('last_modified'),
				last_changed=data.get('last_changed'),
				readme_path=data.get('readme_path'),
				metadata=data.get('metadata', {}),
				audit_status=status,
			)
		except KeyError as e:
			exising_keys = ', '.join(data.keys())
			msg = f'Missing key in data: {e}. Existing keys: {exising_keys}'
			raise KeyError(msg) from e


def build_tree(entry: dict[str, object], keep_children: bool) -> DirectoryNode:
	source = entry.get('source_directory', {})
	root_data = source.get('data')
	if not root_data or 'path' not in root_data:
		raise ValueError('Audit entry has no source directory path.')
	root_node = DirectoryNode.from_dict(root_data, status=source.get('status', 'error'))
	if not keep_children:
		# if source_only is True, we only want the root node
		return root_node

	for dir_entry in entry.get('directories', {}).values():
		child_data = dir_entry.get('data')
		if child_data and 'path' in child_data:
			child_node = DirectoryNode.from_dict(
				child_data, status=dir_entry.get('status', 'error')
			)
			root_node.children.append(child_node)

	return root_node


def flatten_tree(
	root_node: DirectoryNode,
	ignore_keys: list[str] = None,
	metadata_keys: list[str] = None,
) -> list[dict]:
	"""Flatten tree into list of dicts (for DataFrame export)."""
	if metadata_keys is None:
		metadata_keys = ['OWNER', 'DESC', 'EMAIL', 'DATE']
	flat = [asdict(root_node)]
	for child in root_node.children:
		flat.extend(flatten_tree(child))
	if not ignore_keys:
		return flat
	for node in flat:
		# since metadata is a dictionary, remove it, and then insert it by
		# combining the key with the parent key
		if 'metadata' in node and node['metadata']:
			for key, value in node['metadata'].items():
				if key in metadata_keys:
					# create a new key with the prefix 'metadata_'
					node[f'meta.{key}'] = value
			del node['metadata']

		for key in ignore_keys:
			if key in node:
				del node[key]
	return flat


@click.command()
@click.argument('project_group', type=str)
@click.option(
	'--force', '-f', is_flag=True, help='Force collection even if summary exists'
)
@click.option(
	'--keep-children',
	is_flag=True,
	help='Only collect source directories (aka higher level directories only)',
)
def collect_audits(
	project_group: str, force: bool = False, keep_children: bool = False
) -> None:
	"""Collect audits for a project group (after full-audit).

	keep_children: If

	"""
	import json

	from bytesize import ByteSize

	logger.info(f'Collecting audits for project group: {project_group}')
	latest_results_dir = get_latest_result_directory(project_group)

	summary_path = latest_results_dir / f'{project_group}-audit_summary.csv'

	if summary_path.exists() and not force:
		logger.info(f'Summary already exists at {summary_path}, skipping collection.')

		# just print it so that it can be piped to another command!
		click.echo(summary_path.as_posix())
		return

	audit_jsons = list(set(latest_results_dir.rglob('audit.json')))
	if not audit_jsons:
		logger.warning('No audit.json files found')
		return

	forest = []
	skipped = []
	for path in audit_jsons:
		try:
			entry = json.loads(path.read_text())
			# Skip if source_directory has an error
			if not entry.get('source_directory', {}).get('data', {}).get('path'):
				skipped.append(path)
				continue
			tree = build_tree(entry, keep_children=keep_children)
			forest.append(tree)
		except Exception as e:
			logger.error(f'Failed to process {path}: {e}')

	logger.info(f'Processed {len(forest)} audit trees, skipped {len(skipped)} entries')

	ignore_keys = [
		'children',
		# idk what else to ignore rn
	]

	# Optional: flatten all trees to a DataFrame
	flattened = [
		row for tree in forest for row in flatten_tree(tree, ignore_keys=ignore_keys)
	]
	audit_df = pd.DataFrame(flattened)

	# add column for GB size from "size" column
	if 'size' in audit_df.columns:
		# bytesize .GB helper function to convert bytes to GB
		audit_df['size_gb'] = audit_df['size'].apply(
			lambda x: f'{ByteSize(x).GB:.5f}' if pd.notna(x) else None
		)

	# reorder cols this order for better readability
	cols = [
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
	audit_df = audit_df.reindex(columns=cols)

	# drop duplicates based on all columns except 'file_types'
	audit_df = audit_df.drop_duplicates(
		subset=[col for col in cols if col != 'file_types']
	)

	# get duplicated rows based on 'path' and 'name' (this is a problem!!!)
	duplicates = audit_df.duplicated(subset=['path', 'name'], keep=False)
	if duplicates.any():
		logger.warning(
			'Found duplicated rows based on "path" and "name". '
			'These will be kept in the summary.'
		)

		duplicates = audit_df[duplicates]
		logger.info(f'Number of duplicated rows: {len(duplicates)}')

	# sort the DataFrame by 'path' and 'name' for better readability
	audit_df = audit_df.sort_values(
		by=['path'], key=lambda x: x.str.lower(), ascending=True
	).reset_index(drop=True)

	# just echo the csv path
	audit_df.to_csv(summary_path, index=False)
	logger.info(f'Summary written to {summary_path.as_posix()}')

	# just print it so that it can be piped to another command!
	click.echo(summary_path.as_posix())


if __name__ == '__main__':
	collect_audits()  # Run the CLI command when this script is executed directly
