import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : Example - Wired RSS One Title
// Nodes   : 3  |  Connections: 2
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// ManualTrigger                      manualTrigger
// ReadWiredRss                       rssFeedRead
// KeepFirstTitle                     code
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// ManualTrigger
//    → ReadWiredRss
//      → KeepFirstTitle
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'OJ9K7iAHNnrw2GMI',
    name: 'Example - Wired RSS One Title',
    active: false,
    settings: { callerPolicy: 'workflowsFromSameOwner', availableInMCP: false },
})
export class ExampleWiredRssOneTitleWorkflow {
    // =====================================================================
    // CONFIGURATION DES NOEUDS
    // =====================================================================

    @node({
        name: 'Manual Trigger',
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [260, 300],
    })
    ManualTrigger = {};

    @node({
        name: 'Read Wired RSS',
        type: 'n8n-nodes-base.rssFeedRead',
        version: 1.2,
        position: [520, 300],
    })
    ReadWiredRss = {
        url: 'https://www.wired.com/feed/rss',
    };

    @node({
        name: 'Keep First Title',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [780, 300],
    })
    KeepFirstTitle = {
        mode: 'runOnceForAllItems',
        jsCode: 'if (!items.length) return [];\nreturn [{ json: { title: items[0].json.title } }];',
    };

    // =====================================================================
    // ROUTAGE ET CONNEXIONS
    // =====================================================================

    @links()
    defineRouting() {
        this.ManualTrigger.out(0).to(this.ReadWiredRss.in(0));
        this.ReadWiredRss.out(0).to(this.KeepFirstTitle.in(0));
    }
}
