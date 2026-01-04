# Web App (dev)

Runs local Flask UI that submits background jobs which call the existing CLIs.

## Run

- `python apps/webapp/app.py`
- Open `http://127.0.0.1:5000/`

Outputs are written by the underlying commands (typically under `roadways/out/`) and are downloadable from the job page.

