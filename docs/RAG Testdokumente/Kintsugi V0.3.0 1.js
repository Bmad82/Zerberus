```javascript
/**
 * ==============================================================================
 * PROJEKT KINTSUGI - COPYRIGHT NOTICE
 * ==============================================================================
 * Copyright (c) 2025 Chris. All Rights Reserved.
 *
 * NOTICE: This file contains proprietary logic including specific psycho-dynamic
 * modules (Gisela-Core, Peter-Lab, Monkey-Will-Judge constructs).
 * Unauthorized copying, modification, distribution, or use of this code
 * or its underlying concepts is strictly prohibited.
 *
 * This code is strictly proprietary and closed source.
 * ==============================================================================
 */

(async function() {
    'use strict';

    const extensionName = "Kintsugi";
    const KINTSUGI_SETTINGS_KEY = 'kintsugi_settings';

    try {
        window.addEventListener('error', function(event) {
            console.error('[Kintsugi Global Error]', event.error || event.message, event);
        });
        window.addEventListener('unhandledrejection', function(event) {
            console.error('[Kintsugi Unhandled Rejection]', event.reason || event, event);
        });
        console.log(`[${extensionName}] Global error handlers attached.`);
    } catch (e) {
        console.error(`[${extensionName}] Failed to attach global error handlers!`, e);
    }

    const ErUda = {
        config: null, logPanel: null,
        log(message) { if (this.config?.verboseConsole) console.log(`[${extensionName}] ${message}`); if (this.config?.logDecisions && this.logPanel) this._appendToPanel(`[LOG] ${message}`); },
        warn(message) { console.warn(`[${extensionName}] ${message}`); if (this.logPanel) this._appendToPanel(`[WARN] ${message}`, '#fbbf24'); },
        error(message, error = null) { console.error(`[${extensionName}] ${message}`, error); if (this.logPanel) this._appendToPanel(`[ERROR] ${message}`, '#ef4444'); },
        logDecision(decision) { if (this.config?.logDecisions && this.logPanel) { const log = `[${new Date().toLocaleTimeString()}] DECISION: ${decision.speakerName} (${decision.confidence}%) - ${decision.rationale}`; this._appendToPanel(log); } },
        _appendToPanel(text, color = '#0f0') { if (this.logPanel) { const timestamp = new Date().toLocaleTimeString(); const logEntry = $(`<div style="color: ${color}; font-family: monospace; font-size: 10px;">[${timestamp}] ${text}</div>`); this.logPanel.append(logEntry); this.logPanel.scrollTop(this.logPanel[0].scrollHeight); } },
        clearLogPanel() { if (this.logPanel) { this.logPanel.empty(); this._appendToPanel("[Log Cleared]"); } }
    };

    class KintsugiState {
        constructor() {
            this.version = '0.3.0';
            this.enabled = false;
            this.mode = 'sequential';
            this.currentSpeaker = null;
            this.speakerQueue = [];
            this.roundCount = 0;
            
            this.lockQueue = [];
            this.isLocked = false;

            this.characters = new Map();
            this.messages = [];
            this.buffer = new Map();

            this.metrics = {
                totalTokens: 0, interventions: 0, loopsDetected: 0,
                decisionsMade: 0, apiCalls: 0, errors: 0
            };

            this.config = this.getDefaultConfig();
            this.emergencyStopActive = false;
        }

        getDefaultConfig() {
            return {
                performanceLevel: 'standard',
                dirigent: {
                    strategy: 'round-robin', inactivityThreshold: 3,
                    thinkPause: 250, autoAdvance: true, maxWaitTime: 30000
                },
                heiler: {
                    enabled: true, jaccardThreshold: 0.7, maxRepeats: 2,
                    interventionStyle: 'subtle', checkBeforeDecision: true
                },
                gatekeeper: {
                    enabled: true, strictMode: true, maxRetries: 3, lockTimeout: 15000
                },
                chronist: {
                    windowSize: 20, autoSummarize: false
                },
                debug: {
                    logDecisions: true, logAPIcalls: true, verboseConsole: false, showMetrics: true
                }
            };
        }

        applyPerformanceLevel() {
            switch (this.config.performanceLevel) {
                case 'minimal':
                    this.config.chronist.windowSize = 10;
                    this.config.heiler.enabled = false;
                    ErUda.log("Performance Level set to MINIMAL (Window=10, Heiler=Off)");
                    break;
                case 'full':
                    this.config.chronist.windowSize = 30;
                    this.config.heiler.enabled = true;
                    ErUda.log("Performance Level set to FULL (Window=30, Heiler=On)");
                    break;
                case 'standard':
                default:
                    this.config.chronist.windowSize = 20;
                    this.config.heiler.enabled = true;
                    ErUda.log("Performance Level set to STANDARD (Window=20, Heiler=On)");
                    break;
            }
        }

        addCharacter(id, name, avatar = null, priority = 5) {
            if (!this.characters.has(id)) {
                this.characters.set(id, {
                    id, name, avatar, priority,
                    lastSpokenRound: -1, messageCount: 0, loopCount: 0,
                    repeatBuffer: [], isActive: true,
                    stats: { avgResponseTime: 0, totalTokens: 0, lastResponseTime: 0 }
                });
                ErUda.log(`Character added: ${name} (${id})`);
            }
        }

        updateCharacter(id, updates) {
            const char = this.characters.get(id);
            if (char) Object.assign(char, updates);
        }

        async addMessage(author, text, role = 'char') {
            const tokens = await this.getExactTokenCount(text);
            const msg = {
                id: `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                author, role, text,
                timestamp: new Date().toISOString(),
                round: this.roundCount,
                tokens: tokens,
                metadata: {}
            };
            this.messages.push(msg);
            const char = Array.from(this.characters.values()).find(c => c.name === author);
            if (char) {
                char.repeatBuffer.push(text);
                if (char.repeatBuffer.length > 3) char.repeatBuffer.shift();
            }
            return msg;
        }

        getRecentMessages(count = 10) {
            return this.messages.slice(-count);
        }

        async acquireLock() {
            return new Promise((resolve, reject) => {
                const timeoutId = setTimeout(() => {
                    const index = this.lockQueue.indexOf(request);
                    if (index > -1) {
                        this.lockQueue.splice(index, 1);
                    }
                    ErUda.warn(`GATEKEEPER: Lock timeout after ${this.config.gatekeeper.lockTimeout}ms!`);
                    reject(new Error('Lock acquisition timeout'));
                }, this.config.gatekeeper.lockTimeout);

                const request = { resolve, reject, timeoutId };
                this.lockQueue.push(request);

                if (this.lockQueue.length === 1 && !this.isLocked) {
                    this._processNextInQueue();
                }
            });
        }

        _processNextInQueue() {
            if (this.lockQueue.length === 0 || this.isLocked) {
                return;
            }

            this.isLocked = true;
            const request = this.lockQueue[0];
            
            clearTimeout(request.timeoutId);
            request.resolve(true);
            
            ErUda.log(`GATEKEEPER: Lock acquired. Queue length: ${this.lockQueue.length - 1}`);
        }

        releaseLock() {
            if (!this.isLocked) {
                ErUda.warn('GATEKEEPER: Attempted to release lock that was not held');
                return;
            }

            this.lockQueue.shift();
            this.isLocked = false;
            
            ErUda.log(`GATEKEEPER: Lock released. Remaining queue: ${this.lockQueue.length}`);

            if (this.lockQueue.length > 0) {
                this._processNextInQueue();
            }
        }

        async getExactTokenCount(text) {
            try {
                if (typeof SillyTavern !== 'undefined' && SillyTavern.getTokenCountAsync) {
                    const count = await SillyTavern.getTokenCountAsync(text);
                    if (typeof count === 'number' && count >= 0) return count;
                    throw new Error("getTokenCountAsync returned invalid value");
                } else if (window.getTokenCountAsync) {
                    const count = await window.getTokenCountAsync(text);
                    if (typeof count === 'number' && count >= 0) return count;
                    throw new Error("getTokenCountAsync returned invalid value");
                } else {
                    ErUda.warn('getTokenCountAsync not found. Estimating.');
                    return Math.ceil(text.split(/\s+/).length * 1.3);
                }
            } catch (error) {
                ErUda.error('Token count failed', error);
                return Math.ceil(text.split(/\s+/).length * 1.3);
            }
        }
    }

    class Gatekeeper {
        constructor(state) {
            this.state = state;
            this.activeRequests = new Set();
        }

        async requestAccess(operation, retries = 0) {
            if (!this.state.config.gatekeeper.enabled) {
                return await operation();
            }

            const requestId = `req_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
            
            try {
                this.activeRequests.add(requestId);
                
                await this.state.acquireLock();
                
                ErUda.log(`GATEKEEPER: Executing operation [${requestId}]`);
                const result = await operation();
                
                return result;
                
            } catch (error) {
                ErUda.error(`GATEKEEPER: Operation failed [${requestId}]`, error);
                
                if (retries < this.state.config.gatekeeper.maxRetries) {
                    ErUda.warn(`GATEKEEPER: Retrying operation [${requestId}] (${retries + 1}/${this.state.config.gatekeeper.maxRetries})`);
                    await new Promise(resolve => setTimeout(resolve, 500 * (retries + 1)));
                    return await this.requestAccess(operation, retries + 1);
                }
                
                throw error;
                
            } finally {
                this.activeRequests.delete(requestId);
                this.state.releaseLock();
            }
        }

        releaseAccess() {
            this.activeRequests.clear();
            
            while (this.state.lockQueue.length > 0) {
                const request = this.state.lockQueue.shift();
                clearTimeout(request.timeoutId);
                request.reject(new Error('Gatekeeper released all locks'));
            }
            
            this.state.isLocked = false;
            ErUda.warn('GATEKEEPER: All locks released forcefully');
        }

        getStatus() {
            return {
                enabled: this.state.config.gatekeeper.enabled,
                isLocked: this.state.isLocked,
                queueLength: this.state.lockQueue.length,
                activeRequests: this.activeRequests.size
            };
        }
    }

    class GiselaCore {
        constructor(state) { this.state = state; }
        analyze(context) {
            const recentMsgs = context.messages.slice(-5);
            if (!recentMsgs || recentMsgs.length === 0) return { needsIntervention: false };
            const scores = { tension: 0, engagement: 0, toxicity: 0, stagnation: 0 };
            const wordCounts = recentMsgs.map(m => (m.text || '').split(/\s+/).length);
            const avgWordCount = wordCounts.reduce((a, b) => a + b, 0) / wordCounts.length;
            if (avgWordCount < 10) scores.stagnation += 20;
            const speakerChanges = new Set(recentMsgs.map(m => m.author)).size;
            if (speakerChanges < 2 && recentMsgs.length > 2) scores.stagnation += 30;
            const lastText = (recentMsgs[recentMsgs.length - 1]?.text || '').toLowerCase();
            if (lastText.includes('...') || lastText.includes('silence')) scores.tension += 15;
            if (lastText.match(/\b(hate|angry|upset|frustrated|damn|wtf|idiot)\b/i)) scores.toxicity += 25;
            if (lastText.includes('?')) scores.engagement += 10;
            scores.engagement = Math.min(100, Math.max(0, 50 - scores.stagnation + (speakerChanges * 10)));
            const needsIntervention = scores.stagnation > 40 || scores.toxicity > 20;
            return { scores, needsIntervention, diagnosis: this._generateDiagnosis(scores) };
        }
        _generateDiagnosis(scores) {
            if (scores.stagnation > 50) return "Conversation has stalled. Characters might need a prompt or new topic.";
            if (scores.toxicity > 30) return "High tension detected. Consider steering dialogue to neutral ground.";
            if (scores.engagement < 30) return "Low engagement. Characters seem disinterested.";
            return "Conversation flow appears healthy.";
        }
    }

    class PeterLab {
        constructor(state) {
            this.state = state;
            this.SLOP_DICTIONARY = [
                "tapestry", "shimmered", "flickered", "unsettlingly", 
                "barely above a whisper", "testament to", "realm of", 
                "dance of", "symphony of", "prick of desire", 
                "shiver down", "camaraderie", "energetisches Vakuum", 
                "infraschall"
            ];
        }

        detectSlop(text) {
            if (!text) return [];
            const lowerText = text.toLowerCase();
            const foundSlop = [];
            
            for (const slopWord of this.SLOP_DICTIONARY) {
                if (lowerText.includes(slopWord.toLowerCase())) {
                    foundSlop.push(slopWord);
                }
            }
            
            return foundSlop;
        }

        detectPatterns(context) {
            const recentMsgs = context.messages.slice(-10);
            const patterns = { loops: [], repetitions: [], anomalies: [] };
            const speakerSequence = recentMsgs.map(m => m.author);
            for (let i = 0; i < speakerSequence.length - 2; i++) {
                if (speakerSequence[i] === speakerSequence[i + 1]) {
                    patterns.loops.push({ type: 'self-reply', speaker: speakerSequence[i], position: i });
                }
            }
            const textFragments = recentMsgs.map(m => (m.text || '').toLowerCase().slice(0, 50));
            for (let i = 0; i < textFragments.length - 1; i++) {
                for (let j = i + 1; j < textFragments.length; j++) {
                    if (textFragments[i] === textFragments[j] && textFragments[i].length > 10) {
                        patterns.repetitions.push({ text: textFragments[i], positions: [i, j] });
                    }
                }
            }
            return patterns;
        }

        analyzeTokenDistribution(context) {
            const charTokens = new Map();
            context.messages.forEach(msg => {
                const char = msg.author;
                charTokens.set(char, (charTokens.get(char) || 0) + (msg.tokens || 0));
            });
            const total = Array.from(charTokens.values()).reduce((a, b) => a + b, 0);
            const distribution = {};
            charTokens.forEach((tokens, char) => { distribution[char] = total > 0 ? (tokens / total * 100).toFixed(1) : 0; });
            return distribution;
        }
    }

    class Heiler {
        constructor(state, peterLab) { this.state = state; this.peterLab = peterLab; }
        async checkForInterventionNeeded(context) {
            if (!this.state.config.heiler.enabled) return { needed: false };
            const patterns = this.peterLab.detectPatterns(context);
            if (patterns.loops.length > 0 || patterns.repetitions.length > 1) {
                ErUda.warn(`HEILER: Detected ${patterns.loops.length} loops and ${patterns.repetitions.length} repetitions.`);
                return { needed: true, reason: 'loop_detected', patterns };
            }
            const lastFewMessages = context.messages.slice(-3);
            const uniqueSpeakers = new Set(lastFewMessages.map(m => m.author)).size;
            if (uniqueSpeakers === 1 && lastFewMessages.length >= 3) {
                ErUda.warn(`HEILER: Character is speaking to themselves (${lastFewMessages[0].author}).`);
                return { needed: true, reason: 'self-conversation', speaker: lastFewMessages[0].author };
            }
            return { needed: false };
        }
        async intervene(reason, context) {
            ErUda.warn(`HEILER: Intervention triggered. Reason: ${reason}`);
            this.state.metrics.interventions++;
            if (reason === 'loop_detected') {
                return { action: 'reset_buffer', message: 'Detected conversation loop. Resetting internal buffer.' };
            } else if (reason === 'self-conversation') {
                return { action: 'force_speaker_change', message: 'Character speaking to self. Forcing turn change.' };
            }
            return { action: 'none', message: 'Intervention acknowledged but no action taken.' };
        }
    }

    class Dirigent {
        constructor(state, giselaCore, peterLab) { this.state = state; this.giselaCore = giselaCore; this.peterLab = peterLab; }
        async decideNextSpeaker(context) {
            this.state.metrics.decisionsMade++;
            const activeChars = Array.from(this.state.characters.values()).filter(c => c.isActive);
            if (activeChars.length === 0) { ErUda.error("DIRIGENT: No active characters!"); return null; }
            const strategy = this.state.config.dirigent.strategy;
            let nextSpeaker = null;
            if (strategy === 'round-robin') {
                nextSpeaker = this._roundRobin(activeChars, context);
            } else if (strategy === 'priority-based') {
                nextSpeaker = this._priorityBased(activeChars, context);
            } else if (strategy === 'gisela-guided') {
                nextSpeaker = await this._giselaGuided(activeChars, context);
            } else {
                nextSpeaker = this._roundRobin(activeChars, context);
            }
            if (nextSpeaker) {
                nextSpeaker.lastSpokenRound = this.state.roundCount;
                nextSpeaker.messageCount++;
                this.state.currentSpeaker = nextSpeaker.id;
            }
            return nextSpeaker;
        }
        _roundRobin(chars, context) {
            const recentSpeakers = context.messages.slice(-5).map(m => m.author);
            const lastSpeaker = recentSpeakers[recentSpeakers.length - 1];
            const sortedChars = chars.sort((a, b) => {
                const aTurns = a.lastSpokenRound;
                const bTurns = b.lastSpokenRound;
                if (aTurns !== bTurns) return aTurns - bTurns;
                return b.priority - a.priority;
            });
            const candidate = sortedChars[0];
            if (candidate.name === lastSpeaker && sortedChars.length > 1) {
                ErUda.log(`DIRIGENT: Skipping ${candidate.name} (just spoke). Picking next in line.`);
                return sortedChars[1];
            }
            return candidate;
        }
        _priorityBased(chars, context) {
            const sorted = chars.sort((a, b) => b.priority - a.priority);
            return sorted[0];
        }
        async _giselaGuided(chars, context) {
            const analysis = this.giselaCore.analyze(context);
            if (analysis.needsIntervention) {
                ErUda.log(`DIRIGENT: Gisela suggests intervention. Picking high-priority character.`);
                return this._priorityBased(chars, context);
            }
            return this._roundRobin(chars, context);
        }
    }

    class Chronist {
        constructor(state) { this.state = state; }
        async summarizeContext(messages, maxTokens = 500) {
            if (!messages || messages.length === 0) return "No messages to summarize.";
            const recentMessages = messages.slice(-this.state.config.chronist.windowSize);
            let summary = `Recent conversation (last ${recentMessages.length} messages):\n`;
            recentMessages.forEach((msg, idx) => {
                const speaker = msg.author || 'Unknown';
                const text = (msg.text || '').slice(0, 100);
                summary += `${idx + 1}. ${speaker}: ${text}...\n`;
            });
            const currentTokens = await this.state.getExactTokenCount(summary);
            if (currentTokens > maxTokens) {
                const ratio = maxTokens / currentTokens;
                const keepCount = Math.floor(recentMessages.length * ratio);
                return await this.summarizeContext(messages.slice(-keepCount), maxTokens);
            }
            return summary;
        }
        exportHistory(format = 'json') {
            if (format === 'json') {
                return JSON.stringify(this.state.messages, null, 2);
            } else if (format === 'text') {
                return this.state.messages.map(m => `[${m.timestamp}] ${m.author}: ${m.text}`).join('\n');
            }
            return "Unsupported format.";
        }
    }

    class Kintsugi {
        constructor() {
            ErUda.log("Initializing Kintsugi Core...");
            this.state = new KintsugiState();
            this.gatekeeper = new Gatekeeper(this.state);
            this.giselaCore = new GiselaCore(this.state);
            this.peterLab = new PeterLab(this.state);
            this.heiler = new Heiler(this.state, this.peterLab);
            this.dirigent = new Dirigent(this.state, this.giselaCore, this.peterLab);
            this.chronist = new Chronist(this.state);
            this.uiPanel = null;
            this.isProcessing = false;
            this.groupCheckInterval = null;
            this.stContext = null;
            this.isReady = false;
            ErUda.config = this.state.config.debug;
        }

        async init() {
            ErUda.log("Running Kintsugi.init()...");
            try {
                this.buildUI();
                this.attachListeners();
                await this.loadState();
                this.isReady = true;
                ErUda.log("Kintsugi.init() complete. UI built, listeners attached.");
            } catch (error) {
                ErUda.error("CRITICAL: init() failed!", error);
                throw error;
            }
        }

        buildUI() {
            ErUda.log("Building UI Panel...");
            this.uiPanel = $(`
                <div id="kintsugi-panel" style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 15px; border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.3); font-family: 'Segoe UI', sans-serif; color: #e0e0e0; margin: 10px 0;">
                    <div style="display: flex; align-items: center; margin-bottom: 12px; border-bottom: 2px solid #0f3460; padding-bottom: 10px;">
                        <h3 style="margin: 0; font-size: 18px; font-weight: 600; color: #f39c12; text-shadow: 0 2px 4px rgba(0,0,0,0.3);">⚜️ Kintsugi <span style="font-size: 12px; color: #95a5a6; font-weight: 400;">v${this.state.version}</span></h3>
                        <div style="margin-left: auto; display: flex; gap: 8px;">
                            <button id="kintsugi-toggle" style="background: linear-gradient(135deg, #e74c3c, #c0392b); color: white; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 12px; box-shadow: 0 3px 8px rgba(231,76,60,0.4); transition: all 0.3s;">OFF</button>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 12px;">
                        <div style="background: rgba(255,255,255,0.05); padding: 10px; border-radius: 8px; border-left: 3px solid #3498db;">
                            <div style="font-size: 11px; color: #95a5a6; margin-bottom: 4px;">Mode</div>
                            <select id="kintsugi-mode" style="width: 100%; padding: 6px; background: #0f3460; color: #ecf0f1; border: 1px solid #34495e; border-radius: 4px; font-size: 12px;">
                                <option value="sequential">Sequential</option>
                                <option value="dynamic">Dynamic</option>
                            </select>
                        </div>
                        <div style="background: rgba(255,255,255,0.05); padding: 10px; border-radius: 8px; border-left: 3px solid #9b59b6;">
                            <div style="font-size: 11px; color: #95a5a6; margin-bottom: 4px;">Strategy</div>
                            <select id="kintsugi-strategy" style="width: 100%; padding: 6px; background: #0f3460; color: #ecf0f1; border: 1px solid #34495e; border-radius: 4px; font-size: 12px;">
                                <option value="round-robin">Round-Robin</option>
                                <option value="priority-based">Priority</option>
                                <option value="gisela-guided">Gisela-Guided</option>
                            </select>
                        </div>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; font-size: 11px;">
                        <div style="background: rgba(52,152,219,0.2); padding: 8px; border-radius: 6px; text-align: center;">
                            <div style="color: #3498db; font-weight: 600;" id="kintsugi-round">0</div>
                            <div style="color: #95a5a6;">Round</div>
                        </div>
                        <div style="background: rgba(46,204,113,0.2); padding: 8px; border-radius: 6px; text-align: center;">
                            <div style="color: #2ecc71; font-weight: 600;" id="kintsugi-chars">0</div>
                            <div style="color: #95a5a6;">Characters</div>
                        </div>
                        <div style="background: rgba(241,196,15,0.2); padding: 8px; border-radius: 6px; text-align: center;">
                            <div style="color: #f1c40f; font-weight: 600;" id="kintsugi-msgs">0</div>
                            <div style="color: #95a5a6;">Messages</div>
                        </div>
                    </div>
                    <div style="background: rgba(0,0,0,0.3); padding: 10px; border-radius: 8px; margin-bottom: 10px; border: 1px solid #34495e;">
                        <div style="font-size: 11px; color: #95a5a6; margin-bottom: 6px;">Current Speaker</div>
                        <div id="kintsugi-speaker" style="color: #ecf0f1; font-weight: 600; font-size: 13px;">None</div>
                    </div>
                    <details style="margin-bottom: 10px;">
                        <summary style="cursor: pointer; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 6px; font-size: 12px; font-weight: 600; color: #f39c12;">⚙️ Advanced Settings</summary>
                        <div style="padding: 12px; background: rgba(0,0,0,0.2); border-radius: 6px; margin-top: 8px;">
                            <label style="display: block; margin-bottom: 8px; font-size: 11px; color: #95a5a6;">
                                Performance Level:
                                <select id="kintsugi-performance" style="width: 100%; padding: 6px; background: #0f3460; color: #ecf0f1; border: 1px solid #34495e; border-radius: 4px; margin-top: 4px; font-size: 12px;">
                                    <option value="minimal">Minimal</option>
                                    <option value="standard" selected>Standard</option>
                                    <option value="full">Full</option>
                                </select>
                            </label>
                            <label style="display: flex; align-items: center; margin-bottom: 8px; font-size: 12px; color: #ecf0f1;">
                                <input type="checkbox" id="kintsugi-heiler-enabled" checked style="margin-right: 8px;">
                                Enable Heiler (Loop Detection)
                            </label>
                            <label style="display: flex; align-items: center; margin-bottom: 8px; font-size: 12px; color: #ecf0f1;">
                                <input type="checkbox" id="kintsugi-gatekeeper-enabled" checked style="margin-right: 8px;">
                                Enable Gatekeeper (Lock System)
                            </label>
                            <label style="display: flex; align-items: center; margin-bottom: 8px; font-size: 12px; color: #ecf0f1;">
                                <input type="checkbox" id="kintsugi-log-decisions" checked style="margin-right: 8px;">
                                Log Decisions
                            </label>
                            <label style="display: flex; align-items: center; font-size: 12px; color: #ecf0f1;">
                                <input type="checkbox" id="kintsugi-verbose" style="margin-right: 8px;">
                                Verbose Console Logging
                            </label>
                        </div>
                    </details>
                    <details>
                        <summary style="cursor: pointer; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 6px; font-size: 12px; font-weight: 600; color: #f39c12;">📊 Diagnostics</summary>
                        <div style="padding: 12px; background: rgba(0,0,0,0.2); border-radius: 6px; margin-top: 8px;">
                            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; font-size: 11px;">
                                <div>
                                    <span style="color: #95a5a6;">API Calls:</span>
                                    <span id="kintsugi-api-calls" style="color: #3498db; font-weight: 600; margin-left: 5px;">0</span>
                                </div>
                                <div>
                                    <span style="color: #95a5a6;">Interventions:</span>
                                    <span id="kintsugi-interventions" style="color: #e74c3c; font-weight: 600; margin-left: 5px;">0</span>
                                </div>
                                <div>
                                    <span style="color: #95a5a6;">Decisions:</span>
                                    <span id="kintsugi-decisions" style="color: #2ecc71; font-weight: 600; margin-left: 5px;">0</span>
                                </div>
                                <div>
                                    <span style="color: #95a5a6;">Errors:</span>
                                    <span id="kintsugi-errors" style="color: #e67e22; font-weight: 600; margin-left: 5px;">0</span>
                                </div>
                            </div>
                            <div style="max-height: 150px; overflow-y: auto; background: #000; padding: 8px; border-radius: 4px; border: 1px solid #34495e;" id="kintsugi-log-panel"></div>
                            <button id="kintsugi-clear-log" style="width: 100%; margin-top: 8px; background: #34495e; color: white; border: none; padding: 6px; border-radius: 4px; cursor: pointer; font-size: 11px;">Clear Log</button>
                        </div>
                    </details>
                    <div style="display: flex; gap: 8px; margin-top: 12px;">
                        <button id="kintsugi-save-profile" style="flex: 1; background: linear-gradient(135deg, #2ecc71, #27ae60); color: white; border: none; padding: 8px; border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 600; box-shadow: 0 3px 8px rgba(46,204,113,0.4);">Save Profile</button>
                        <button id="kintsugi-load-profile" style="flex: 1; background: linear-gradient(135deg, #3498db, #2980b9); color: white; border: none; padding: 8px; border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 600; box-shadow: 0 3px 8px rgba(52,152,219,0.4);">Load Profile</button>
                        <button id="kintsugi-reset" style="flex: 1; background: linear-gradient(135deg, #e67e22, #d35400); color: white; border: none; padding: 8px; border-radius: 6px; cursor: pointer; font-size: 11px; font-weight: 600; box-shadow: 0 3px 8px rgba(230,126,34,0.4);">Reset</button>
                    </div>
                    <button id="kintsugi-emergency-stop" style="width: 100%; margin-top: 10px; background: linear-gradient(135deg, #c0392b, #e74c3c); color: white; border: none; padding: 10px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 700; box-shadow: 0 4px 12px rgba(231,76,60,0.5); text-transform: uppercase; letter-spacing: 1px;">🛑 Emergency Stop</button>
                </div>
            `);
            ErUda.logPanel = this.uiPanel.find('#kintsugi-log-panel');
            ErUda.log("UI Panel created successfully.");
        }

        attachListeners() {
            ErUda.log("Attaching event listeners...");
            this.uiPanel.find('#kintsugi-toggle').on('click', () => this.toggleEnable());
            this.uiPanel.find('#kintsugi-mode').on('change', (e) => { this.state.mode = $(e.target).val(); this.saveState(); });
            this.uiPanel.find('#kintsugi-strategy').on('change', (e) => { this.state.config.dirigent.strategy = $(e.target).val(); this.saveState(); });
            this.uiPanel.find('#kintsugi-performance').on('change', (e) => { this.state.config.performanceLevel = $(e.target).val(); this.state.applyPerformanceLevel(); this.saveState(); });
            this.uiPanel.find('#kintsugi-heiler-enabled').on('change', (e) => { this.state.config.heiler.enabled = $(e.target).is(':checked'); this.saveState(); });
            this.uiPanel.find('#kintsugi-gatekeeper-enabled').on('change', (e) => { this.state.config.gatekeeper.enabled = $(e.target).is(':checked'); this.saveState(); });
            this.uiPanel.find('#kintsugi-log-decisions').on('change', (e) => { this.state.config.debug.logDecisions = $(e.target).is(':checked'); this.saveState(); });
            this.uiPanel.find('#kintsugi-verbose').on('change', (e) => { this.state.config.debug.verboseConsole = $(e.target).is(':checked'); this.saveState(); });
            this.uiPanel.find('#kintsugi-clear-log').on('click', () => ErUda.clearLogPanel());
            this.uiPanel.find('#kintsugi-save-profile').on('click', () => this.saveProfile());
            this.uiPanel.find('#kintsugi-load-profile').on('click', () => this.loadProfile());
            this.uiPanel.find('#kintsugi-reset').on('click', () => this.resetProfile());
            this.uiPanel.find('#kintsugi-emergency-stop').on('click', () => this.emergencyStop());
        }

        toggleEnable(forceState = null) {
            this.state.enabled = forceState !== null ? forceState : !this.state.enabled;
            const btn = this.uiPanel.find('#kintsugi-toggle');
            if (this.state.enabled) {
                btn.text('ON').css({ background: 'linear-gradient(135deg, #2ecc71, #27ae60)', boxShadow: '0 3px 8px rgba(46,204,113,0.4)' });
                ErUda.log("Kintsugi ENABLED.");
                this.startGroupMonitoring();
            } else {
                btn.text('OFF').css({ background: 'linear-gradient(135deg, #e74c3c, #c0392b)', boxShadow: '0 3px 8px rgba(231,76,60,0.4)' });
                ErUda.log("Kintsugi DISABLED.");
                this.stopGroupMonitoring();
            }
            this.saveState();
        }

        startGroupMonitoring() {
            if (this.groupCheckInterval) return;
            ErUda.log("Starting group chat monitoring...");
            this.groupCheckInterval = setInterval(() => {
                if (!this.state.enabled) return;
                if (this.state.config.dirigent.autoAdvance && !this.isProcessing) {
                    const timeSinceLastMsg = Date.now() - (this._lastMessageTime || 0);
                    if (timeSinceLastMsg > this.state.config.dirigent.maxWaitTime) {
                        ErUda.warn(`Auto-advance triggered (${timeSinceLastMsg}ms since last message).`);
                        try {
                            const stContext = SillyTavern.getContext();
                            if (stContext?.generate) {
                                stContext.generate('kintsugi-autoadvance');
                            }
                        } catch (e) {
                            ErUda.error("Auto-advance failed", e);
                        }
                    }
                }
            }, 5000);
        }

        stopGroupMonitoring() {
            if (this.groupCheckInterval) {
                clearInterval(this.groupCheckInterval);
                this.groupCheckInterval = null;
                ErUda.log("Group chat monitoring stopped.");
            }
        }

        async handleGenerateIntercept(chat, contextSize, abort, type) {
            if (!this.isReady) {
                ErUda.warn("Kintsugi not ready yet. Ignoring intercept.");
                return;
            }
            
            if (!this.state.enabled || this.state.emergencyStopActive) return;
            if (!this.isGroupChat()) {
                ErUda.log(`HOOK: generate_interceptor - not in group chat, ignoring.`);
                return;
            }
            if (this.isProcessing) { 
                ErUda.warn("Already processing. Ignoring intercept."); 
                return; 
            }

            ErUda.log(`HOOK: generate_interceptor triggered (Type: ${type}). Taking control.`);
            abort(true);

            const lastMessage = chat[chat.length - 1];
            if (lastMessage && lastMessage.is_user && !this.state.messages.find(m => m.text === lastMessage.mes)) {
                ErUda.log(`HOOK: Detected new user message. Adding to internal state.`);
                await this.state.addMessage(lastMessage.name, lastMessage.mes, 'user');
            }
            
            setTimeout(() => this.orchestrateConversation(), 0);
        }

        async handleChatLoaded() {
            if (!this.isReady) {
                ErUda.warn("Kintsugi not ready yet. Ignoring chat loaded.");
                return;
            }
            
            ErUda.log(`HOOK: chat_loaded triggered - re-evaluating environment.`);
            try {
                await this.detectEnvironment(); 
                this.resetState(true); 
            } catch(e) {
                ErUda.error("Failed to handle chat_loaded event", e);
            }
        }

        async orchestrateConversation() {
            if (!this.state.enabled || this.isProcessing || this.state.emergencyStopActive) {
                ErUda.warn("Orchestration skipped - system disabled, busy, or emergency stopped.");
                return;
            }

            try {
                this.isProcessing = true;

                await this.gatekeeper.requestAccess(async () => {
                    this.state.roundCount++;
                    ErUda.log(`═══ R${this.state.roundCount} START ═══`);
                    this.updateUI('stats');

                    const context = { 
                        messages: this.state.getRecentMessages(20), 
                        characters: Array.from(this.state.characters.values()) 
                    };
                    
                    let excludeSpeakers = [];
                    if (this.state.config.heiler.checkBeforeDecision) {
                        const interventionCheck = await this.heiler.checkForInterventionNeeded(context);
                        if (interventionCheck.needed) {
                            const interventionResult = await this.heiler.intervene(interventionCheck.reason, context);
                            ErUda.warn(`HEILER intervention: ${interventionResult.message}`);
                            if (interventionResult.action === 'force_speaker_change' && interventionCheck.speaker) {
                                excludeSpeakers.push(interventionCheck.speaker);
                            }
                        }
                    }

                    const decision = await this.dirigent.decideNextSpeaker(context);
                    if (!decision) {
                        this.showError('Dirigent could not decide. No speaker available.');
                        ErUda.error(`Orchestration stopped: Dirigent failed to decide.`);
                        return;
                    }
                    
                    ErUda.log(`DIRIGENT decided: ${decision.name}`);
                    this.state.currentSpeaker = decision.id;
                    this.updateUI('speaker');

                    const contextMessages = await this.chronist.summarizeContext(this.state.messages, 4096);
                    const promptPayload = this.buildPromptPayload(decision.id, contextMessages);

                    const responseText = await this.callCharacterAPI(decision.id, promptPayload);

                    if (responseText) {
                        const slopWords = this.peterLab.detectSlop(responseText);
                        if (slopWords.length > 0) {
                            ErUda.warn(`ANTISLOP HIT: [${slopWords.join(', ')}]`);
                            ErUda.log(`[ANTISLOP DETECTED]`);
                        }

                        await this.state.addMessage(decision.name, responseText, 'char');
                        this.appendMessageToChat(decision.name, responseText, false);
                        
                        this.updateUI('stats');
                        ErUda.log(`Message from ${decision.name} added to chat.`);
                    } else {
                        ErUda.warn(`API call for ${decision.name} returned no response text.`);
                    }

                    ErUda.log(`═══ R${this.state.roundCount} END ═══`);

                    if (this.state.config.dirigent.autoAdvance && this.state.enabled) {
                        setTimeout(() => {
                            if (this.state.enabled && !this.isProcessing) {
                                ErUda.log("Auto-advance triggering next round...");
                                try {
                                    const stContext = SillyTavern.getContext();
                                    if (stContext?.generate) {
                                        stContext.generate('kintsugi-autoadvance');
                                    }
                                } catch (e) {
                                    ErUda.error("Auto-advance generation failed", e);
                                }
                            }
                        }, 1000);
                    }
                });

            } catch (error) {
                ErUda.error(`Orchestration failed in Round ${this.state.roundCount}:`, error);
                this.state.metrics.errors++;
                this.showError(`Generation process failed: ${error.message}`);
                this.updateUI('metrics');
            } finally {
                this.isProcessing = false;
            }
        }

        buildPromptPayload(speakerId, contextSummary) {
            const char = this.state.characters.get(speakerId);
            if (!char) return null;

            const messages = [
                { role: 'system', content: contextSummary },
                { role: 'user', content: `Continue the conversation as ${char.name}.` }
            ];

            return {
                prompt: messages,
                characterId: speakerId,
                overrideName: char.name
            };
        }

        async callCharacterAPI(charId, payload) {
            const char = this.state.characters.get(charId);
            if (!char || !payload) return null;

            ErUda.log(`API Call for ${char.name} using internal ST function.`);
            this.state.metrics.apiCalls++;
            const startTime = performance.now();

            try {
                const context = SillyTavern.getContext();
                let responseText = '';

                if (context.generateQuietPrompt) {
                    const promptString = payload.prompt.map(msg => {
                        if (msg.role === 'system') return `[SYSTEM: ${msg.content}]`;
                        const prefix = msg.name && !['user', 'assistant'].includes(msg.name.toLowerCase()) ? `${msg.name}: ` : '';
                        return `${prefix}${msg.content}`;
                    }).join('\n');
                    const finalPrompt = `${promptString}\n${char.name}:`;

                    if (this.state.config.debug.logAPIcalls) {
                        ErUda.log(`Prompt for ${char.name} (${finalPrompt.length} chars):\n---\n${finalPrompt.substring(0, 300)}...\n---`);
                    }

                    responseText = await context.generateQuietPrompt(finalPrompt, false, false, { characterId: charId });
                } else {
                    ErUda.warn(`generateQuietPrompt not found. Trying simulation fallback...`);
                    responseText = await this.simulateResponse(char);
                }

                const endTime = performance.now();
                if(char.stats) char.stats.lastResponseTime = endTime - startTime;
                
                if (this.state.config.debug.logAPIcalls) {
                    ErUda.log(`API response for ${char.name} received in ${char.stats?.lastResponseTime?.toFixed(0) || 'N/A'}ms.`);
                }

                return responseText?.trim() || null;
            } catch (error) {
                ErUda.error(`API call failed for ${char.name}:`, error); 
                this.state.metrics.errors++;
                this.updateUI('metrics');
                return null;
            }
        }

        async simulateResponse(char) {
            await new Promise(resolve => setTimeout(resolve, 500));
            return `*${char.name} simuliert eine Antwort.*`;
        }

        appendMessageToChat(author, text, isUser = false) {
            try {
                const context = SillyTavern.getContext();
                const char = Array.from(this.state.characters.values()).find(c => c.name === author);
                const messageData = {
                    name: author,
                    is_user: isUser,
                    is_system: author === 'System',
                    mes: text,
                    swipes: [text],
                    send_date: Date.now(),
                    gen_started: Date.now(),
                    gen_finished: Date.now(),
                    avatar: isUser ? context.userAvatar : (char ? char.avatar : null)
                };

                if (typeof context.addOneMessage === 'function') {
                    context.addOneMessage(messageData);
                } else {
                    ErUda.warn("addOneMessage function not found, using fallback chat.push(). UI might not update correctly.");
                    context.chat?.push(messageData);
                    context.saveChatDebounced?.();
                }
                
                const chatElement = $('#chat');
                if (chatElement.length > 0) {
                    chatElement.scrollTop(chatElement[0].scrollHeight);
                }
            } catch (error) {
                ErUda.error(`Failed to append message via addOneMessage/fallback:`, error);
                this.fallbackAppendMessage(author, text, isUser);
            }
        }

        fallbackAppendMessage(author, text, isUser) {
            try {
                const chatLog = $('#chat');
                if (chatLog.length === 0) {
                    ErUda.error("Fallback append failed: #chat container not found.");
                    return;
                }
                const context = SillyTavern.getContext();
                const avatar = isUser ? context?.userAvatar : Array.from(this.state.characters.values()).find(c => c.name === author)?.avatar;
                const avatarHtml = avatar ? `<img src="/img/${avatar}" class="char_avatar" style="width: 40px; height: 40px; border-radius: 50%;">` : '<div style="width: 40px;"></div>';
                const messageHtml = `
                    <div class="mes ${isUser ? 'user_mes' : 'char_mes'}" style="display: flex; margin-bottom: 10px; align-items: flex-end;">
                        ${!isUser ? avatarHtml : ''}
                        <div class="mes_block" style="background: ${isUser ? '#d1e4ff' : '#f0f0f0'}; color: #333; padding: 10px; border-radius: 10px; max-width: 70%; margin: 0 5px;">
                            <div class="name" style="font-weight: bold; margin-bottom: 3px;">${author}</div>
                            <div class="mes_text">${text.replace(/\n/g, '<br>')}</div>
                        </div>
                        ${isUser ? avatarHtml : ''}
                    </div>`;
                chatLog.append(messageHtml);
                chatLog.scrollTop(chatLog[0].scrollHeight);
            } catch (fallbackError) {
                ErUda.error("CRITICAL: Even fallbackAppendMessage failed!", fallbackError);
            }
        }

        async detectEnvironment() {
            if (typeof SillyTavern === 'undefined' || !SillyTavern.getContext) {
                throw new Error('SillyTavern context not found!');
            }
            this.stContext = SillyTavern.getContext();
            
            await this.loadState();
            this.state.applyPerformanceLevel();
            
            if (!this.isGroupChat()) { 
                ErUda.log(`Not in group chat - standing by`); 
                return; 
            }
            this.detectCharacters();
        }

        detectCharacters() {
            this.state.characters.clear();
            try {
                const context = this.stContext || SillyTavern.getContext();
                if (!context) { 
                    ErUda.error("Cannot detect characters: SillyTavern context is unavailable."); 
                    return; 
                }

                const group = context.groups?.find(g => g.id === context.groupId);
                if (group && group.members && context.characters) {
                    group.members.forEach((memberInfo, index) => {
                        const charId = typeof memberInfo === 'object' ? memberInfo.id : memberInfo;
                        const char = context.characters.find(c => c.avatar === charId);
                        if (char) {
                            const uniqueId = char.chid || char.avatar || char.name;
                            this.state.addCharacter(uniqueId, char.name, char.avatar, 5 + index);
                        } else { 
                            ErUda.warn(`Character data for ID ${charId} not found in context.characters.`); 
                        }
                    });
                } else if (!group) { 
                    ErUda.warn(`Current group (ID: ${context.groupId}) not found in context.groups.`); 
                } else if (!context.characters) { 
                    ErUda.warn(`context.characters is missing or empty.`); 
                }

                if (context.name1) { 
                    this.state.addCharacter('user', context.name1, null, 10); 
                }

                const charCount = this.state.characters.size;
                ErUda.log(`Detected ${charCount} participants.`);
                this.updateUI('stats');
            } catch (error) { 
                ErUda.error(`Character detection failed:`, error); 
            }
        }

        isGroupChat() {
            try {
                const context = this.stContext || SillyTavern.getContext();
                return context?.groupId != null;
            } catch(e) {
                ErUda.error("Error checking group chat status", e);
                return false;
            }
        }

        resetState(keepConfig = true) {
            const currentConfig = keepConfig ? deepMerge({}, this.state.config) : null;
            const currentEnabled = this.state.enabled;
            this.state = new KintsugiState();
            if (keepConfig && currentConfig) {
                this.state.config = currentConfig;
                this.state.enabled = currentEnabled;
                this.state.applyPerformanceLevel();
            }
            ErUda.config = this.state.config.debug;
            
            if(this.uiPanel) {
                ErUda.logPanel = this.uiPanel.find('#kintsugi-log-panel');
            }

            this.detectCharacters();
            this.updateUI('stats');
            this.updateUI('settings');
            ErUda.log(`Kintsugi session state reset (Config kept: ${keepConfig})`);
        }

        updateUI(target = 'all') {
            if (!this.uiPanel) return;
            
            if (target === 'all' || target === 'stats') {
                this.uiPanel.find('#kintsugi-round').text(this.state.roundCount);
                this.uiPanel.find('#kintsugi-chars').text(this.state.characters.size);
                this.uiPanel.find('#kintsugi-msgs').text(this.state.messages.length);
            }
            if (target === 'all' || target === 'speaker') {
                const currentChar = this.state.characters.get(this.state.currentSpeaker);
                this.uiPanel.find('#kintsugi-speaker').text(currentChar ? currentChar.name : 'None');
            }
            if (target === 'all' || target === 'metrics') {
                this.uiPanel.find('#kintsugi-api-calls').text(this.state.metrics.apiCalls);
                this.uiPanel.find('#kintsugi-interventions').text(this.state.metrics.interventions);
                this.uiPanel.find('#kintsugi-decisions').text(this.state.metrics.decisionsMade);
                this.uiPanel.find('#kintsugi-errors').text(this.state.metrics.errors);
            }
            if (target === 'all' || target === 'settings') {
                this.uiPanel.find('#kintsugi-mode').val(this.state.mode);
                this.uiPanel.find('#kintsugi-strategy').val(this.state.config.dirigent.strategy);
                this.uiPanel.find('#kintsugi-performance').val(this.state.config.performanceLevel);
                this.uiPanel.find('#kintsugi-heiler-enabled').prop('checked', this.state.config.heiler.enabled);
                this.uiPanel.find('#kintsugi-gatekeeper-enabled').prop('checked', this.state.config.gatekeeper.enabled);
                this.uiPanel.find('#kintsugi-log-decisions').prop('checked', this.state.config.debug.logDecisions);
                this.uiPanel.find('#kintsugi-verbose').prop('checked', this.state.config.debug.verboseConsole);
            }
        }

        saveState() {
            try {
                const stateData = {
                    version: this.state.version,
                    enabled: this.state.enabled,
                    mode: this.state.mode,
                    config: this.state.config
                };
                localStorage.setItem(KINTSUGI_SETTINGS_KEY, JSON.stringify(stateData));
                ErUda.log("State saved to localStorage.");
            } catch (error) {
                this.showError(`Failed to save state: ${error.message}`);
            }
        }

        async loadState() {
            try {
                const savedState = localStorage.getItem(KINTSUGI_SETTINGS_KEY);
                if (savedState) {
                    const stateData = JSON.parse(savedState);
                    if (stateData.version && stateData.version.startsWith('0.')) {
                        this.state.enabled = stateData.enabled || false;
                        this.state.mode = stateData.mode || 'sequential';
                        if (stateData.config) {
                            deepMerge(this.state.config, stateData.config);
                        }
                        this.state.applyPerformanceLevel();
                        this.updateUI('all');
                        ErUda.log("State loaded from localStorage.");
                    } else {
                        ErUda.warn(`Version mismatch. Saved: ${stateData.version}, Current: ${this.state.version}. Using defaults.`);
                    }
                } else {
                    ErUda.log("No saved state in localStorage.");
                }
            } catch (error) {
                this.showError(`Failed to load state: ${error.message}`);
            }
        }

        showError(message) {
            ErUda.error(message);
            if (window.toastr) {
                toastr.error(message, extensionName, { timeOut: 5000 });
            }
        }

        saveProfile() {
            try {
                const profileData = {
                    version: this.state.version,
                    config: this.state.config,
                    timestamp: new Date().toISOString()
                };
                localStorage.setItem('kintsugi_profile', JSON.stringify(profileData));
                ErUda.log("Profile configuration saved to localStorage.");
                if (window.toastr) toastr.success("Settings profile saved locally.", extensionName);
            } catch (error) {
                this.showError(`Failed to save profile: ${error.message}`);
            }
         }

        loadProfile() {
            try {
                const savedProfile = localStorage.getItem('kintsugi_profile');
                if (savedProfile) {
                    const profileData = JSON.parse(savedProfile);
                    if (profileData.config) {
                        deepMerge(this.state.config, profileData.config);
                        this.state.config.performanceLevel = profileData.config.performanceLevel || this.state.getDefaultConfig().performanceLevel;

                        this.state.applyPerformanceLevel();
                        this.updateUI('settings');
                        
                        this.saveState(); 
                        ErUda.log("Profile loaded from localStorage and applied.");
                        if (window.toastr) toastr.success("Settings profile loaded.", extensionName);
                    } else { ErUda.warn("Loaded profile data is invalid or missing 'config'."); }
                } else {
                    ErUda.log("No profile found in localStorage.");
                    if (window.toastr) toastr.info("No saved profile found.", extensionName);
                }
            } catch (error) {
                this.showError(`Failed to load profile: ${error.message}`);
            }
         }

        resetProfile() {
            if (confirm('Reset ALL Kintsugi settings to their default values? The saved profile in localStorage will also be cleared.')) {
                this.state.config = this.state.getDefaultConfig();
                this.state.applyPerformanceLevel();
                
                this.updateUI('settings');

                this.saveState();
                try {
                    localStorage.removeItem('kintsugi_profile');
                    ErUda.log("Settings reset to defaults. Saved profile cleared.");
                    if (window.toastr) toastr.success("Settings reset to defaults. Profile cleared.", extensionName);
                } catch (error) {
                    this.showError(`Failed to clear saved profile: ${error.message}`);
                }
            }
         }

        emergencyStop() {
            ErUda.warn(`╔═══════════ EMERGENCY STOP ═══════════╗`);
            ErUda.warn(`║ Halting Kintsugi Systems Immediately! ║`);
            ErUda.warn(`╚════════════════════════════════════╝`);
            this.state.emergencyStopActive = true;
            this.isProcessing = false;
            this.toggleEnable(false);

            try {
                $(document).off('.kintsugi');
                clearInterval(this.groupCheckInterval);
                this.groupCheckInterval = null;
                ErUda.log("Kintsugi event listeners removed.");
            } catch(e) { ErUda.error("Error removing Kintsugi listeners during emergency stop", e); }

            this.gatekeeper.releaseAccess();
            this.showError('EMERGENCY STOP - Kintsugi halted! Please reload SillyTavern.');
            setTimeout(() => {
                this.state.emergencyStopActive = false;
                ErUda.log("Emergency stop lock released (internal flag only).");
            }, 5000);
         }
    }

    function deepMerge(target, source) {
        if (typeof target !== 'object' || target === null || typeof source !== 'object' || source === null) {
            return target;
        }
        for (const key in source) {
            if (Object.prototype.hasOwnProperty.call(source, key)) {
                const sourceValue = source[key];
                const targetValue = target[key];
                if (sourceValue && typeof sourceValue === 'object' && !Array.isArray(sourceValue) && Object.prototype.toString.call(sourceValue) === '[object Object]' &&
                    targetValue && typeof targetValue === 'object' && !Array.isArray(targetValue) && Object.prototype.toString.call(targetValue) === '[object Object]') {
                    deepMerge(targetValue, sourceValue);
                }
                else if (sourceValue !== undefined) {
                    target[key] = sourceValue;
                }
            }
        }
        return target;
    }

    async function waitForSillyTavern(maxRetries = 50) {
        let retries = 0;
        while (retries < maxRetries) {
            if (typeof SillyTavern !== 'undefined' && SillyTavern.getContext) {
                ErUda.log(`SillyTavern detected after ${retries} retries.`);
                return true;
            }
            ErUda.log(`Waiting for SillyTavern... (${retries + 1}/${maxRetries})`);
            await new Promise(resolve => setTimeout(resolve, 100));
            retries++;
        }
        throw new Error('SillyTavern not available after maximum retries');
    }

    async function initializeKintsugi() {
        try {
            ErUda.log(`Deploying Kintsugi v0.3.0...`);
            
            await waitForSillyTavern();
            
            if (window.KintsugiInstance) {
                ErUda.warn(`Kintsugi already initialized.`);
                return;
            }

            window.KintsugiInstance = new Kintsugi();

            window.Kintsugi_generateInterceptor = async function(chat, contextSize, abort, type) {
                if (window.KintsugiInstance && window.KintsugiInstance.isReady) {
                    await window.KintsugiInstance.handleGenerateIntercept(chat, contextSize, abort, type);
                } else {
                    ErUda.warn("Kintsugi instance not ready for intercept");
                }
            };

            window.Kintsugi_chatLoaded = async function() {
                if (window.KintsugiInstance && window.KintsugiInstance.isReady) {
                    await window.KintsugiInstance.handleChatLoaded();
                } else {
                    ErUda.warn("Kintsugi instance not ready for chat loaded");
                }
            };

            let eventSource, event_types;
            
            try {
                const extensionModule = await import('/scripts/extensions.js');
                eventSource = extensionModule.eventSource;
                event_types = extensionModule.event_types;
            } catch (importError) {
                ErUda.warn("Dynamic import failed, trying global eventSource", importError);
                if (window.eventSource && window.event_types) {
                    eventSource = window.eventSource;
                    event_types = window.event_types;
                } else {
                    throw new Error('Could not access eventSource');
                }
            }

            eventSource.on(event_types.APP_READY, async () => {
                console.log(`[${extensionName}] SillyTavern APP_READY event received. Finalizing Kintsugi initialization...`);
                
                try {
                    await window.KintsugiInstance.init();

                    if (document.readyState === 'complete' || document.readyState === 'interactive') {
                        injectUI();
                    } else {
                        window.addEventListener('DOMContentLoaded', injectUI);
                    }

                    function injectUI() {
                        const existing = document.getElementById('kintsugi-panel');
                        if (existing) existing.remove();

                        if (typeof $ === 'undefined') {
                            ErUda.error("jQuery not available for UI injection");
                            return;
                        }

                        $('body').append(window.KintsugiInstance.uiPanel);

                        window.KintsugiInstance.uiPanel.css({
                            'position': 'fixed',
                            'top': '40px',
                            'right': '10px',
                            'width': 'auto',
                            'max-width': '90%',
                            'z-index': '10000',
                            'max-height': '80vh',
                            'overflow-y': 'auto'
                        });

                        ErUda.log("UI Panel injected directly as overlay.");
                    }
                    
                    await window.KintsugiInstance.handleChatLoaded();
                    
                    ErUda.log(`╔════════════════════════════════════════╗`);
                    ErUda.log(`║  KINTSUGI V0.3.0 DEPLOYED SUCCESSFULLY!  ║`);
                    ErUda.log(`╚════════════════════════════════════════╝`);
                    if(window.toastr) toastr.success('Extension ready!', `Kintsugi v${window.KintsugiInstance.state.version}`, { timeOut: 3000 });
                    
                } catch (e) {
                    console.error(`[${extensionName}] Failed during APP_READY initialization:`, e);
                    if (window.alert) alert(`Kintsugi Critical Error: Failed to initialize after APP_READY. Check console. ${e?.message}`);
                }
            });

        } catch (e) {
            console.error(`[${extensionName}] Failed to initialize Kintsugi:`, e);
            if(window.alert) alert(`Kintsugi Error: Initialization failed. Check console for details. ${e?.message}`);
        }
    }

    initializeKintsugi();

})();
```