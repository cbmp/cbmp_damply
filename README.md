# CBMP_DaMPly
This project is a fork of bhklab/DaMPly (Data Management Plan)

## Overview
DaMPly (Data Management Plan) is a Python module designed to streamline the process of managing and packaging directories. This fork has been modified to audit any group directories on H4H. See the [changelog](https://github.com/cbmp/cbmp_damply/blob/main/docs/CHANGELOG.md) for more details about these modifications.

## Installing DaMPly
Connect to build node on H4H and install:
```bash
salloc -p build -c 1 --mem=250M -t 0:10:0
uv tool install git+https://github.com/cbmp/cbmp_damply.git
```

## Data Audting
To run a full data audit on a group folder on H4H, perform the following steps:

**Step 1:** Connect to the compute node (unless submitting the audit job via `sbatch`, see below) Set `--mem` and `--time` according to the size of the directory being audited. For reference, a group folder containing 20k directories takes ~4h with default settings:
```bash
salloc -c 1 --mem=5G --time 5:00:0
```
**Step 2:** Run `full-audit`. Runing this will first build a list of all directories to audit by scanning through all contents of the group folder. Once a list of directories is built, each item will be submitted for audit as an `sbatch` job. The memory and time limits for these jobs can be changed with `--sbatch-time` (01:00:00 by default) and `--sbatch-memory` (4G by default). Changing these limits is highly recommended for group folders containing deeply nested directories. Additionally, `full-audit` will limit the number of pending+running jobs to 2000 to avoid QOS limits on H4H. Different limits can be specified with `--max-jobs`. The audit results will be stored in the group folder under `<group_folder>/admin/audit/logs/<date>/...`.
```bash
damply full-audit <group_folder>
```
Alternatively run `full-audit` via `sbatch` (recommended for large group directories). See sample script below:
```
#!/bin/bash

#SBATCH -t 5:00:00
#SBATCH --mem=5G
#SBATCH -J <group_folder>_audit
#SBATCH -p all
#SBATCH -c 1
#SBATCH -o <group_folder>_audit_<date>.out

damply full-audit <group_folder>
```
**Tips:**
Individual directories may fail to be audited due to sbatch time limits, sbatch memory limits and lack of read permissions. Once `full-audit` is completed, directories that failed to be audited due to one of the above reasons can be identified by running:
```bash
sacct -u "$USER" --starttime=<audityear-month-day> --format=JobID,JobName%-80,State,Elapsed,Timelimit | awk 'NR == 1 || $3 ~ /^TIMEOUT/'
sacct -u "$USER" --starttime=<audityear-month-day> --format=JobID,JobName%-80,State,Elapsed,Timelimit | awk 'NR == 1 || $3 ~ /^FAILED/'
```
Paths to directories you do not have read permissions for (and which will not be included in the audit) can be displayed by running:
```bash
find <group_folder> -type d ! -readable
```


**Step 3:** Run `collect-audits` to collect the audit results. The results will be stored under `<group_folder>/admin/audit/results/<date>/...`. A `<group_folder>-audit_summary.csv` will be generated summarizing the owner, size, last modified date and file extensions for all audited directories in the group folder. This does not require a lot of time or memory to run.
```bash
damply collect-audits <group_folder>
```

**Step 4:** Run `plot` to create a sankey plot showing a graphical breakdown of data usage in the audited directory. Plot aesthetics can be changed by supplying `--height`, `--node-pad`, `--label-size` to improve readability. The directories plotted can by filtered through `--threshold_gb` and `--max-depth-from-root`.
```bash
damply plot <path>/<to>/admin/audit/results/<date>/<group_folder>-audit_summary.csv
```

## Standardzing Access to Project Directories
DAmPly also has functions to to standardize file path naming systems avoid the issues that come with differences in directory naming systems and project file structures between users running a script. These functions have not been modified in this DAmPly fork. More details about these functions can be found at bhklab/damply: https://bhklab.github.io/damply/dmpdirs/