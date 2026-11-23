# CBMP_DaMPly
This project is a fork of bhklab/DaMPly (Data Management Plan).

## Overview
DaMPly (Data Management Plan) is a Python module designed to streamline the process of managing and packaging directories. This fork has modified DaMPly to audit any group directories on H4H with a file path of `cluster/projects/...`. See the [changelog](https://github.com/cbmp/cbmp_damply/blob/main/docs/CHANGELOG.md) for more details about these modifications.

## Installing DaMPly
Connect to the build node on H4H and install:
```bash
salloc -p build -c 1 --mem=250M -t 0:10:0
uv tool install git+https://github.com/cbmp/cbmp_damply.git
```

## Data Audting
To run a full data audit on a group folder on H4H it is recommended to use the `batch-audit` workflow not the `full-audit` workflow (see next section). To run `batch-audit`, perform the following steps:

**Step 1:** Run `batch-audit`. Running this will first build a list of all directories to audit by scanning through the group folder. Once the directory list is built, directory metadata is collected through the `du` command submitted by a series of `sbatch` jobs.  
Use `--fanout-threshold` to control how far a directory tree extends within each `du` command. If a directory has fewer immediate children than this threshold (1000 by default), the whole tree stays together as one recursive root and is audited with a single `du` command. For a directory that meets this threshold, its children become separate roots, so the large subtree can be audited across multiple `du` commands. Use this parameter to avoid auditing very large/nested directories as a single recursive root which is likely to exceed reasonable time and memory limits.   
Use `--batch-size` to control how many of those roots are audited in each `sbatch` job (1000 by default). The memory and time limits for these jobs can be changed with `--sbatch-time` (01:00:00 by default) and `--sbatch-memory` (4G by default). The directories audited in each `sbatch` job can be found in the .json files stored at `<group_folder>/admin/audit/jobs/<date>/...`.  

See the example below:
```bash
# Simple example directory tree
Sample_Dir
    ├── Folder A
    |    ├── Child Folder A
    |    ├── Child Folder B
    |    ├── Child Folder C
    |    └── Child Folder D
    └── Folder B
    |    ├── Child Folder E
    |    ├── Child Folder F
    |    └── Child Folder G
    └── Folder C
         └── Child Folder H

# Run batch-audit on Sample_Dir
damply batch-audit --fanout-threshold 4 --batch-size 2 Sample_Dir

# Running the above command will audit the directories in Sample_Dir with the following sbatch jobs:
# Job 1: Sample_Dir/Folder A/Child Folder A
#        Sample_Dir/Folder A/Child Folder B
# Job 2: Sample_Dir/Folder A/Child Folder C
#        Sample_Dir/Folder A/Child Folder D
# Job 3: Sample_Dir/Folder B
#        Sample_Dir/Folder C
# Job 4: Sample_Dir/
#        Sample_Dir/Folder A  

# Run batch-audit on Sample_Dir using different settings
damply batch-audit --fanout-threshold 2 --batch-size 2 Sample_Dir

# Running the above command will audit the directories in Sample_Dir with the following sbatch jobs:
# Job 1: Sample_Dir/Folder A/Child Folder A
#        Sample_Dir/Folder A/Child Folder B
# Job 2: Sample_Dir/Folder A/Child Folder C
#        Sample_Dir/Folder A/Child Folder D
# Job 3: Sample_Dir/Folder B/Child Folder E
#        Sample_Dir/Folder B/Child Folder F
# Job 4: Sample_Dir/Folder B/Child Folder G
#        Sample_Dir/Folder B
# Job 5: Sample_Dir/Folder C
#        Sample_Dir/Folder A
# Job 6: Sample_Dir/
```
**Note:**
It is *highly* recommended to submit `damply batch-audit` as an `sbatch` job as opposed to running it in an interactive shell. This command can take a lot of time to run. For reference, a group folder with ~5 million subdirectories takes ~1.5 days with MaxRSS ~3GB.   
**Another Note:**
Directories you do not have read permissions for will fail to be audited. Paths to directories you do not have read permissions for can be displayed by running the command below before starting the audit:
```bash
find <group_folder> -type d ! -readable
``` 

**Step 2:** Run `collect-batch-audits` to collect the audit results. The results will be stored in `<group_folder>-batch-audit_summary.csv` under `<group_folder>/admin/audit/results/<date>/...`. This file reports the owner, size, last modified date and file extensions for all audited directories in the group folder. If a directory displays `error` in the `audit_status` column, some part of that directory's audit failed. This can mean a size wasn’t returned by `du`, or an error occurred lower in the tree (usually due to lack of read permissions). If a directory displays `TRUE` in the `size_is_estimated` column, the size is estimated from the parent directory’s direct files, plus the sizes reported for its immediate child directories.
```bash
# Run collect-batch-audits. This takes less time and memory to run compared to the previous step.
salloc -c 1 --mem=5G --time 1:00:0
damply collect-batch-audits <group_folder>
```
**Step 3:** Run `plot` to create a sankey plot showing a graphical breakdown of data usage in the audited directory. Plot aesthetics can be changed by supplying `--height`, `--node-pad` and `--label-size` to improve readability. The directories plotted can by filtered with `--threshold_gb` and `--max-depth-from-root`.
```bash
# Run plot
damply plot <path>/<to>/admin/audit/results/<date>/<group_folder>-audit_summary.csv
```
**Note:** If you have directories in your audit summary that failed to be audited, they may have no values in their `size`, `size_gb` and/or `file_count` columns. To avoid an error with `damply plot`, fill these empty cells with `0` before running. This can be done using the command below
```bash
awk -F, 'BEGIN {OFS=","} {for (i=6; i<=9; i++) if ($i== "") $i = 0; print}' <group_folder>-audit_summary.csv > <group-folder>-batch-audit_summary_noempty.csv
```
## DaMPly Full-Audit (Deprecated)
Audits can also be run on a group folder via `full-audit` and these results can be collected with `collect-audits`. However, it is *highly* recommended to use `batch-audit` instead following the steps above. Running `full-audit` will submit an `sbatch` job for *every* folder within in the group directory. A group directory containing thousands of folders will therefore be audited across thousands of jobs. Waiting for these jobs to commence running is a major bottleneck in the auditing process. For example, running `full-audit` on `Sample_Dir` above will submit 12 jobs! Thus it is better to use `batch-audit` to group multiple folders for audit and thus submit much fewer jobs.
## Standardzing Access to Project Directories
DAmPly also has functions to to standardize file path naming systems. These functions help avoid the issues that come with differences in directory naming systems and project file structures between different users running the same script. These functions have not been modified in this DAmPly fork. More details about these functions can be found at bhklab/damply: https://bhklab.github.io/damply/dmpdirs/