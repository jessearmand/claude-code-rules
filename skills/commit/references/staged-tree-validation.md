# Staged-tree validation

Use when excluded working-tree content could affect validation, or when exact-index
validation is required by the repository, user, or staged-only workflow.

1. Record the candidate with `git write-tree`. Stop on unmerged index entries.
2. Materialize that tree in a distinct Git checkout or worktree under the permitted
   scratch location. Use Git tree/index checkout machinery to preserve binary files,
   symlinks, and modes. A plain directory inside the dirty worktree is insufficient
   if tools discover the parent repository or its configuration.
3. Run repository setup and relevant checks with the isolated checkout as the
   working directory. Do not copy excluded source or configuration into it, or
   let symlinks, environment settings, or scripts resolve source from the original
   dirty worktree. Setup may create dependencies, caches, and build outputs.
4. If the candidate depends on excluded source, report the missing dependency.
   For a general commit, correct it within the authorized scope and validate the
   revised tree. For a staged-only request, do not extend the index without approval.
5. Compare the current index tree with the validated tree before committing.
   Investigate differences and validate the revised candidate when necessary.
6. Remove only the temporary checkout and resources created for this validation,
   preserving the original index and unrelated user files.

If the environment cannot support these checks, report the limitation instead of
claiming the candidate is independently validated.
