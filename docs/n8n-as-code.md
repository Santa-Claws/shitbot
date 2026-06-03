# n8n-as-code workflow management

The `automations/` directory uses [n8n-as-code](https://github.com/nicholasgasior/n8n-as-code) to keep the n8n workflow tracked in git as TypeScript. This lets you version-control, diff, and restore workflow changes without manually exporting JSON from the UI.

## Structure

```
automations/
├── n8nac-config.json       # points to your local n8n instance
├── package.json
└── workflows/
    └── local_5678_mira_c/personal/
        └── Shitpost Bot - Reddit Weekly to Discord.workflow.ts
```

## Setup

Install dependencies once:

```bash
cd automations
npm install
```

The n8n-as-code tool reads your API key from its own global config (not stored in this repo). If you haven't set it up yet:

```bash
npx n8nac init
```

It will ask for your n8n URL and API key. The API key lives in n8n under **Settings → API → Create an API key**.

## Common commands

```bash
# List all workflows (local and remote)
npm run list

# List workflows on the remote n8n instance
npm run list:remote

# Pull a workflow from n8n into the local file
npm run pull -- --workflowsid <WORKFLOW_ID>

# Push local changes back to n8n
npm run push -- --workflowsid <WORKFLOW_ID>
```

The workflow ID for the shitpost bot is `1y22EifIaGNPK3hJ` — visible in the n8n UI URL when the workflow is open.

## Typical workflow for making changes

1. Edit the `.workflow.ts` file directly, or make changes in the n8n UI
2. If you edited in the UI, pull to sync: `npm run pull -- --workflowsid 1y22EifIaGNPK3hJ`
3. Commit the `.workflow.ts` file
4. If you edited the file directly, push to apply: `npm run push -- --workflowsid 1y22EifIaGNPK3hJ`

## Rotating the n8n API key

If you generate a new API key in n8n, re-run init to update the global config:

```bash
npx n8nac init
```

The API key is not stored in this repo — it lives only in the n8n-as-code global config on the machine running the commands.
