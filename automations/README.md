# n8n Automations as Code

This folder is initialized with `n8n-as-code` and connected to your local n8n instance.

## Quick start

```bash
cd /home/ycantplay/n8n/automations
npm run list
```

## Pull an existing workflow from n8n

1. Get the workflow ID from the n8n UI URL (or `npm run list -- --remote`).
2. Pull it:

```bash
npm run pull -- --workflowsid <WORKFLOW_ID>
```

The workflow file is saved under `workflows/`.

## Edit and push back to n8n

```bash
npm run push -- --workflowsid <WORKFLOW_ID>
```

## Useful commands

```bash
npm run list
npm run list:remote
npm run update-ai
```

## Notes

- Project config is in `n8nac-config.json`.
- API key is stored in n8n-as-code global config, not this repo.
- If you rotate your n8n API key, re-run:

```bash
npx n8nac init
```
