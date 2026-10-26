# CBMP_DaMPly
This project is a fork of bhklab/DaMPly (Data Management Plan)

## Overview
DaMPly (Data Management Plan) is a Python module designed to streamline the process of managing and packaging directories. This fork has been modified to audit package directories on H4H outside of BHK Lab. See the [changelog](https://github.com/cbmp/cbmp_damply/blob/main/docs/CHANGELOG.md) for more details about these modifications.

## Install Damply
Connect to build node on H4H and install:
```bash
salloc -p build -c 1 --mem=250M -t 0:10:0
uv tool install git+https://github.com/cbmp/cbmp_damply.git
```

## Data Audting
To run a full data audit on a group folder, perform the following steps:

**Step 1:** Connect to the compute node (unless submitting the audit job via `sbatch`, see below):
```bash
salloc -c 1 --mem=5G --time 5:00:0
```
**Step 2:** Run `full-audit`. Runing this will first build a list of all directories to audit by scanning through all contents of the group folder. Group folders with a large number of directories will require higher `--mem` and `--time` to run to completion. Once a list of directories is built, each item will be submitted for audit as an `sbatch` job. By default, `full-audit` will limit the number of pending+running jobs to 2000 to avoid QOS limits on H4H. Different limits can be specified with `--max-jobs`. The results will be stored in your group folder under `<group_folder>/admin/audit/logs/<date>/...`. Audit jobs will not be submitted for directories within `<group_folder>/admin/audit...`.
```bash
damply full-audit <group_folder>
```
Alternatively run `full-audit` via `sbatch`. See sample script below (recommended for group directories with thousands of files):
```bash
#!/bin/bash

#SBATCH -t 5:00:00
#SBATCH --mem=5G
#SBATCH -J <group_folder>_audit
#SBATCH -p all
#SBATCH -c 1
#SBATCH -o <group_folder>_audit_<date>.out

damply full-audit <group_folder>
```
Note that audits can only be performed on folders you have read permissions for. You can find folders you do not have read access to using the following command. The contents of folders listed here will not be audited.
```bash
find <group_folder> -type d ! -readable
```

**Step 3:** Run `collect-audits` to collect the audit results. The results will be stored under `<group_folder>/admin/audit/results/<date>/...`. A `<group_folder>-audit_summary.csv` will be generated summarizing the owner, size, last modified date and file extensions for all audited directories in the group folder.
```bash
damply collect-audits <group_folder>
```

**Step 4:** Run `plot` to create a sankey plot showing a graphical breakdown of data usage in the audited directory.
```bash
damply plot <path>/<to>/admin/audit/results/<date>/<group_folder>-audit_summary.csv
```

## Standardzing Access to Project Directories
DAmPly also has functions to to standardize file path naming systems avoid the issues that come with differences in directory naming systems and project file structures between users running a script. These functions have not been modified in this DAmPly fork. More details about these functions can be found at bhklab/damply: https://bhklab.github.io/damply/dmpdirs/