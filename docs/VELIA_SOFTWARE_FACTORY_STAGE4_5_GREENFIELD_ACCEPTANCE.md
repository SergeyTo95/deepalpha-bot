# Stage 4.5 acceptance

Merge only when all of the following are true:

- exact-head GitHub workflows are green;
- dedicated Stage 4.5 tests compile and pass;
- the PR preview starts successfully on Railway;
- startup logs show greenfield runtime + hardening installed with `enabled=false`;
- production variables are unchanged;
- no greenfield module contains a GitHub repository-write primitive;
- repository attach remains exact-name and same-installation only;
- empty repositories are rejected until they have an initial commit.
