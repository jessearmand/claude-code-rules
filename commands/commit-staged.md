# Commit Staged

Commit staged changes with clear and concise message

## Primary Task:
Run `git diff --staged`, review the changes, commit the staged changes with clear and concise message.

## Important:
- DO NOT add any other files that are not staged
- Focus on the staged changes identified by the output of `git diff --staged`
- EXCEPTION: when there are modified files from the process of checking, ONLY add those modified files

## Process:
1. Perform checks with /check or refer to project setup for running checks, tests, or linting
2. After checks are resolved, proceed with reviewing the output of `git diff`
3. Review modified files from the process of checking, ONLY add those modified files
4. Run `git diff --staged`, then ONLY commit the staged changes

