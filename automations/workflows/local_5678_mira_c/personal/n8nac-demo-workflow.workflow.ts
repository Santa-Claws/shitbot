import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : n8nac-demo-workflow
// Nodes   : 2  |  Connections: 1
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// WhenClickingTestWorkflow           manualTrigger
// SetMessage                         set
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// WhenClickingTestWorkflow
//    → SetMessage
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'mNkvQ1RTCJq6286Z',
    name: 'n8nac-demo-workflow',
    active: false,
    settings: { callerPolicy: 'workflowsFromSameOwner', availableInMCP: false, executionOrder: 'v1' },
})
export class N8nacDemoWorkflow {
    // =====================================================================
    // CONFIGURATION DES NOEUDS
    // =====================================================================

    @node({
        name: "When clicking 'Test workflow'",
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [260, 300],
    })
    WhenClickingTestWorkflow = {};

    @node({
        name: 'Set Message',
        type: 'n8n-nodes-base.set',
        version: 3.4,
        position: [520, 300],
    })
    SetMessage = {
        assignments: {
            assignments: [
                {
                    id: 'msg',
                    name: 'message',
                    type: 'string',
                    value: 'hello from n8n-as-code',
                },
            ],
        },
        options: {},
    };

    // =====================================================================
    // ROUTAGE ET CONNEXIONS
    // =====================================================================

    @links()
    defineRouting() {
        this.WhenClickingTestWorkflow.out(0).to(this.SetMessage.in(0));
    }
}
