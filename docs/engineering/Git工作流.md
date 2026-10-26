# Git 工作流

The project repository is:

`https://github.com/lingqitangyuan-jpg/RSI-Towards-LongHorizon.git`

The local default branch is `main`. Keep source code, manifests, tests, protocols, and small research notes under version control. Keep generated runs, checkpoints, model weights, raw/derived datasets, credentials, local reference repositories, and downloaded PDFs out of ordinary Git commits. Their provenance belongs in run metadata or experiment records.

Typical local workflow:

```powershell
git status --short
git switch -c experiment/appworld-pilot
python -m unittest discover -s tests -v
git diff --check
git add src tests configs scripts docs README.md README.en.md README.zh.md pyproject.toml .gitignore
git commit -m "Describe the reproducible change"
git switch main
git merge --no-ff experiment/<short-name>
git push -u origin main
```

For a real experiment, commit the configuration and protocol before running it, record the resulting commit in the experiment row, and commit analysis code separately from large outputs. Do not rewrite published experiment history. If the remote is unavailable, keep the local commit and record the failed synchronization attempt; do not treat an unpushed commit as a remote backup.
