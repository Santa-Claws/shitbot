import { workflow, node, links } from '@n8n-as-code/transformer';

// <workflow-map>
// Workflow : Shitpost Bot - Reddit Weekly to Discord
// Nodes   : 15  |  Connections: 17
//
// NODE INDEX
// ──────────────────────────────────────────────────────────────────
// Property name                    Node type (short)         Flags
// ManualTrigger                      manualTrigger
// ScheduleTrigger                    scheduleTrigger
// FetchFeedPool                      httpRequest                [onError→regular]
// BuildCandidateQueue                code
// SelectAttemptCandidates            httpRequest                [onError→regular]
// ExpandSelectedCandidates           code
// LoopCandidates                     splitInBatches
// PrepareAttempt                     httpRequest                [onError→regular]
// IfDownloadReady                    if
// ReadDownloadedMedia                readWriteFile              [onError→regular]
// IfBinaryLoaded                     if
// MarkReadFailure                    code
// UploadDiscordFile                  httpRequest                [onError→regular]
// NormalizeDiscordOutcome            code
// FinalizeAttempt                    httpRequest                [onError→regular]
//
// ROUTING MAP
// ──────────────────────────────────────────────────────────────────
// ManualTrigger
//    → FetchFeedPool
//      → BuildCandidateQueue
//        → SelectAttemptCandidates
//          → ExpandSelectedCandidates
//            → LoopCandidates
//             .out(1) → PrepareAttempt
//                → IfDownloadReady
//                  → ReadDownloadedMedia
//                    → IfBinaryLoaded
//                      → UploadDiscordFile
//                        → NormalizeDiscordOutcome
//                          → FinalizeAttempt
//                            → LoopCandidates (↩ loop)
//                     .out(1) → MarkReadFailure
//                        → FinalizeAttempt (↩ loop)
//                 .out(1) → LoopCandidates (↩ loop)
// ScheduleTrigger
//    → FetchFeedPool (↩ loop)
// </workflow-map>

// =====================================================================
// METADATA DU WORKFLOW
// =====================================================================

@workflow({
    id: 'eDNQVrqtgzoMbgHw',
    name: 'Shitpost Bot - Reddit Weekly to Discord',
    active: false,
    settings: {
        timezone: 'America/Los_Angeles',
        executionOrder: 'v1',
        callerPolicy: 'workflowsFromSameOwner',
        availableInMCP: false,
    },
})
export class ShitpostBotRedditWeeklyToDiscordWorkflow {
    // =====================================================================
    // CONFIGURATION DES NOEUDS
    // =====================================================================

    @node({
        name: 'Manual Trigger',
        type: 'n8n-nodes-base.manualTrigger',
        version: 1,
        position: [260, 220],
    })
    ManualTrigger = {};

    @node({
        name: 'Schedule Trigger',
        type: 'n8n-nodes-base.scheduleTrigger',
        version: 1.3,
        position: [260, 420],
    })
    ScheduleTrigger = {
        rule: {
            interval: [
                {
                    field: 'hours',
                    hoursInterval: 4,
                },
            ],
        },
    };

    @node({
        name: 'Fetch Feed Pool',
        type: 'n8n-nodes-base.httpRequest',
        version: 4.2,
        position: [540, 320],
        onError: 'continueRegularOutput',
    })
    FetchFeedPool = {
        authentication: 'none',
        method: 'POST',
        url: '={{ (($env.SHITPOST_SERVICE_URL || "").endsWith("/") ? ($env.SHITPOST_SERVICE_URL || "").slice(0, -1) : ($env.SHITPOST_SERVICE_URL || "")) + "/api/feed-pool" }}',
        sendBody: true,
        contentType: 'json',
        specifyBody: 'json',
        jsonBody:
            '{"subreddits":["MemeVideos","MurderedByWords","blursed_videos","Kitchencels","addressme","CursedGuns","perfectlycutvideos"]}',
        options: {
            timeout: 30000,
            response: {
                response: {
                    responseFormat: 'json',
                },
            },
        },
    };

    @node({
        name: 'Build Candidate Queue',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [800, 320],
    })
    BuildCandidateQueue = {
        mode: 'runOnceForAllItems',
        language: 'javaScript',
        jsCode: 'const payload = items[0]?.json ?? {};\nconst queue = Array.isArray(payload.items) ? payload.items : [];\nif (!queue.length) {\n  return [];\n}\n\nconst seedText = new Date().toISOString().slice(0, 13);\nlet seed = 0;\nfor (const char of seedText) {\n  seed = (seed * 31 + char.charCodeAt(0)) >>> 0;\n}\nconst nextRandom = () => {\n  seed = (seed * 1664525 + 1013904223) >>> 0;\n  return seed / 0x100000000;\n};\n\nconst shuffledQueue = [...queue];\nfor (let i = shuffledQueue.length - 1; i > 0; i -= 1) {\n  const j = Math.floor(nextRandom() * (i + 1));\n  [shuffledQueue[i], shuffledQueue[j]] = [shuffledQueue[j], shuffledQueue[i]];\n}\n\nreturn [{\n  json: {\n    queue: shuffledQueue,\n    selectionLimit: 10,\n    fetchedAt: payload.fetchedAt ?? null,\n    feedErrors: Array.isArray(payload.errors) ? payload.errors : [],\n    shuffleSeed: seedText,\n  },\n}];',
    };

    @node({
        name: 'Select Attempt Candidates',
        type: 'n8n-nodes-base.httpRequest',
        version: 4.2,
        position: [1060, 320],
        onError: 'continueRegularOutput',
    })
    SelectAttemptCandidates = {
        authentication: 'none',
        method: 'POST',
        url: '={{ (($env.SHITPOST_SERVICE_URL || "").endsWith("/") ? ($env.SHITPOST_SERVICE_URL || "").slice(0, -1) : ($env.SHITPOST_SERVICE_URL || "")) + "/api/select-attempts" }}',
        sendBody: true,
        contentType: 'json',
        specifyBody: 'json',
        jsonBody: '={{ JSON.stringify({ queue: $json.queue || [], limit: 10 }) }}',
        options: {
            timeout: 30000,
            response: {
                response: {
                    responseFormat: 'json',
                },
            },
        },
    };

    @node({
        name: 'Expand Selected Candidates',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [1320, 320],
    })
    ExpandSelectedCandidates = {
        mode: 'runOnceForAllItems',
        language: 'javaScript',
        jsCode: 'const payload = items[0]?.json ?? {};\nconst selected = Array.isArray(payload.items) ? payload.items : [];\nreturn selected.map((candidate) => ({ json: candidate }));',
    };

    @node({
        name: 'Loop Candidates',
        type: 'n8n-nodes-base.splitInBatches',
        version: 3,
        position: [1580, 320],
    })
    LoopCandidates = {
        batchSize: 1,
        options: {},
    };

    @node({
        name: 'Prepare Attempt',
        type: 'n8n-nodes-base.httpRequest',
        version: 4.2,
        position: [1840, 320],
        onError: 'continueRegularOutput',
    })
    PrepareAttempt = {
        authentication: 'none',
        method: 'POST',
        url: '={{ (($env.SHITPOST_SERVICE_URL || "").endsWith("/") ? ($env.SHITPOST_SERVICE_URL || "").slice(0, -1) : ($env.SHITPOST_SERVICE_URL || "")) + "/api/prepare-attempt" }}',
        sendBody: true,
        contentType: 'json',
        specifyBody: 'json',
        jsonBody: '={{ JSON.stringify($json) }}',
        options: {
            timeout: 130000,
            response: {
                response: {
                    responseFormat: 'json',
                },
            },
        },
    };

    @node({
        name: 'If Download Ready',
        type: 'n8n-nodes-base.if',
        version: 2.3,
        position: [2100, 320],
    })
    IfDownloadReady = {
        conditions: {
            options: {
                caseSensitive: true,
                leftValue: '',
                typeValidation: 'strict',
            },
            conditions: [
                {
                    id: 'download-ready',
                    leftValue: '={{ $json.status }}',
                    rightValue: 'ready',
                    operator: {
                        type: 'string',
                        operation: 'equals',
                    },
                },
            ],
            combinator: 'and',
        },
        options: {},
    };

    @node({
        name: 'Read Downloaded Media',
        type: 'n8n-nodes-base.readWriteFile',
        version: 1.1,
        position: [2360, 220],
        onError: 'continueRegularOutput',
    })
    ReadDownloadedMedia = {
        operation: 'read',
        fileSelector: '={{ $json.sharedPath }}',
        dataPropertyName: 'data',
        options: {},
    };

    @node({
        name: 'If Binary Loaded',
        type: 'n8n-nodes-base.if',
        version: 2.3,
        position: [2620, 220],
    })
    IfBinaryLoaded = {
        conditions: {
            options: {
                caseSensitive: true,
                leftValue: '',
                typeValidation: 'strict',
            },
            conditions: [
                {
                    id: 'binary-present',
                    leftValue:
                        '={{ ($input.item.binary && $input.item.binary.data && $input.item.binary.data.id) ? $input.item.binary.data.id : "" }}',
                    rightValue: '',
                    operator: {
                        type: 'string',
                        operation: 'isNotEmpty',
                    },
                },
            ],
            combinator: 'and',
        },
        options: {},
    };

    @node({
        name: 'Mark Read Failure',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [2880, 360],
    })
    MarkReadFailure = {
        mode: 'runOnceForEachItem',
        language: 'javaScript',
        jsCode: "const payload = $input.item.json ?? {};\nconst paired = $input.item.pairedItem;\nconst pairedIndex = Array.isArray(paired) ? paired[0]?.item : paired?.item;\nlet attemptId = Number(payload.attemptId || 0);\n\nif ((!attemptId || Number.isNaN(attemptId)) && Number.isInteger(pairedIndex)) {\n  try {\n    const source = $items('Prepare Attempt', 0, pairedIndex)?.json ?? {};\n    attemptId = Number(source.attemptId || 0);\n  } catch (e) {}\n}\n\nreturn {\n  json: {\n    ...payload,\n    attemptId: attemptId || 0,\n    outcome: 'read_failed',\n    note: payload.error ? 'read_failed:' + payload.error : 'binary_missing_after_read',\n  },\n};",
    };

    @node({
        name: 'Upload Discord File',
        type: 'n8n-nodes-base.httpRequest',
        version: 4.2,
        position: [2880, 140],
        onError: 'continueRegularOutput',
    })
    UploadDiscordFile = {
        authentication: 'none',
        method: 'POST',
        url: '={{ $env.SHITPOST_DISCORD_WEBHOOK_URL || "" }}',
        sendQuery: true,
        specifyQuery: 'json',
        jsonQuery: '={{ JSON.stringify({ wait: "true" }) }}',
        sendBody: true,
        contentType: 'multipart-form-data',
        bodyParameters: {
            parameters: [
                {
                    parameterType: 'formBinaryData',
                    name: 'file',
                    inputDataFieldName: 'data',
                },
            ],
        },
        options: {
            timeout: 120000,
            response: {
                response: {
                    fullResponse: true,
                    neverError: true,
                    responseFormat: 'json',
                },
            },
        },
    };

    @node({
        name: 'Normalize Discord Outcome',
        type: 'n8n-nodes-base.code',
        version: 2,
        position: [3140, 140],
    })
    NormalizeDiscordOutcome = {
        mode: 'runOnceForEachItem',
        language: 'javaScript',
        jsCode: "const payload = $input.item.json ?? {};\nconst paired = $input.item.pairedItem;\nconst pairedIndex = Array.isArray(paired) ? paired[0]?.item : paired?.item;\nlet attemptId = Number(payload.attemptId || 0);\n\nif ((!attemptId || Number.isNaN(attemptId)) && Number.isInteger(pairedIndex)) {\n  try {\n    const source = $items('Prepare Attempt', 0, pairedIndex)?.json ?? {};\n    attemptId = Number(source.attemptId || 0);\n  } catch (e) {}\n}\n\nconst statusCode = Number(payload.statusCode || payload.code || 0);\nlet outcome = 'discord_failed';\nlet note = 'discord_request_failed';\n\nif (statusCode >= 200 && statusCode < 300) {\n  outcome = 'posted';\n  note = '';\n} else if (statusCode > 0) {\n  note = `discord_status_${statusCode}`;\n} else if (typeof payload.message === 'string' && payload.message) {\n  note = payload.message;\n} else if (typeof payload.error === 'string' && payload.error) {\n  note = payload.error;\n}\n\nreturn {\n  json: {\n    ...payload,\n    attemptId: attemptId || 0,\n    outcome,\n    note,\n  },\n};",
    };

    @node({
        name: 'Finalize Attempt',
        type: 'n8n-nodes-base.httpRequest',
        version: 4.2,
        position: [3400, 260],
        onError: 'continueRegularOutput',
    })
    FinalizeAttempt = {
        authentication: 'none',
        method: 'POST',
        url: '={{ (($env.SHITPOST_SERVICE_URL || "").endsWith("/") ? ($env.SHITPOST_SERVICE_URL || "").slice(0, -1) : ($env.SHITPOST_SERVICE_URL || "")) + "/api/finalize-attempt" }}',
        sendBody: true,
        contentType: 'json',
        specifyBody: 'json',
        jsonBody:
            '={{ JSON.stringify({ attemptId: $json.attemptId || 0, outcome: $json.outcome || "discord_failed", note: $json.note || "" }) }}',
        options: {
            timeout: 30000,
            response: {
                response: {
                    neverError: true,
                    responseFormat: 'json',
                },
            },
        },
    };

    // =====================================================================
    // ROUTAGE ET CONNEXIONS
    // =====================================================================

    @links()
    defineRouting() {
        this.ManualTrigger.out(0).to(this.FetchFeedPool.in(0));
        this.ScheduleTrigger.out(0).to(this.FetchFeedPool.in(0));
        this.FetchFeedPool.out(0).to(this.BuildCandidateQueue.in(0));
        this.BuildCandidateQueue.out(0).to(this.SelectAttemptCandidates.in(0));
        this.SelectAttemptCandidates.out(0).to(this.ExpandSelectedCandidates.in(0));
        this.ExpandSelectedCandidates.out(0).to(this.LoopCandidates.in(0));
        this.LoopCandidates.out(1).to(this.PrepareAttempt.in(0));
        this.PrepareAttempt.out(0).to(this.IfDownloadReady.in(0));
        this.IfDownloadReady.out(0).to(this.ReadDownloadedMedia.in(0));
        this.IfDownloadReady.out(1).to(this.LoopCandidates.in(0));
        this.ReadDownloadedMedia.out(0).to(this.IfBinaryLoaded.in(0));
        this.IfBinaryLoaded.out(0).to(this.UploadDiscordFile.in(0));
        this.IfBinaryLoaded.out(1).to(this.MarkReadFailure.in(0));
        this.UploadDiscordFile.out(0).to(this.NormalizeDiscordOutcome.in(0));
        this.NormalizeDiscordOutcome.out(0).to(this.FinalizeAttempt.in(0));
        this.MarkReadFailure.out(0).to(this.FinalizeAttempt.in(0));
        this.FinalizeAttempt.out(0).to(this.LoopCandidates.in(0));
    }
}
