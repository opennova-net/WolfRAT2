const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const sourcePath = process.argv[2];
if (!sourcePath) {
    throw new Error('usage: node web_client_result_test.js <app.js>');
}

class FakeClassList {
    constructor(initial = []) {
        this.values = new Set(initial);
    }

    add(...names) {
        names.forEach(name => this.values.add(name));
    }

    remove(...names) {
        names.forEach(name => this.values.delete(name));
    }

    contains(name) {
        return this.values.has(name);
    }
}

class FakeElement {
    constructor(id = '') {
        this.id = id;
        this.classList = new FakeClassList();
        this.dataset = {};
        this.style = {};
        this.value = '';
        this.textContent = '';
        this.innerHTML = '';
        this.children = [];
    }

    addEventListener() {}

    appendChild(child) {
        this.children.push(child);
        if (child.id) elements.set(child.id, child);
        return child;
    }

    setAttribute(name, value) {
        this[name] = value;
    }
}

const elements = new Map();
for (const id of [
    'action-player-name',
    'action-sheet',
    'app',
    'chat-input',
    'map-list',
    'player-list',
    'player-count',
]) {
    elements.set(id, new FakeElement(id));
}
elements.get('action-sheet').classList.add('hidden');

const storage = {
    getItem() { return null; },
    setItem() {},
    removeItem() {},
};

class FakeWebSocket {
    static OPEN = 1;

    constructor() {
        this.readyState = 0;
    }

    send() {}
}

const context = vm.createContext({
    console,
    document: {
        body: new FakeElement('body'),
        createElement: () => new FakeElement(),
        getElementById: id => elements.get(id) || null,
        querySelectorAll: () => [],
    },
    localStorage: storage,
    sessionStorage: storage,
    location: { protocol: 'http:', host: 'localhost' },
    WebSocket: FakeWebSocket,
    fetch: async () => {
        throw new Error('unexpected fetch');
    },
    confirm: () => true,
    alert: () => {},
    setTimeout: () => 0,
    clearInterval: () => {},
    setInterval: () => 0,
});

vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, {
    filename: sourcePath,
});

async function main() {
    vm.runInContext(`
        handleWebSocketMessage({
            type: 'command_result',
            accepted: false,
            replies: ['ERROR denied'],
            error: 'ERROR denied'
        });
    `, context);

    const result = elements.get('operation-result');
    assert(result, 'a result surface must be created');
    assert.strictEqual(result.dataset.outcome, 'rejected');
    assert.match(result.textContent, /ERROR denied/);

    vm.runInContext(`
        renderPlayers([{
            id: 7,
            name: 'Alice',
            revision: 6,
            team: '1',
            team_name: 'Joint Ops',
            kills: '4',
        }]);
    `, context);
    const renderedPlayers = elements.get('player-list').innerHTML;
    assert.match(renderedPlayers, /data-player-id="7"/);
    assert.match(renderedPlayers, /data-player-name="Alice"/);
    assert.match(renderedPlayers, /data-player-revision="6"/);

    context.fetch = async () => ({
        ok: true,
        status: 200,
        json: async () => ({
            ok: true,
            accepted: true,
            replies: ['OK'],
            action: 'refresh_players',
        }),
    });
    await vm.runInContext(
        `quickAction('refresh_players', 'Refresh players')`,
        context,
    );

    assert.strictEqual(result.dataset.outcome, 'accepted');
    assert.match(result.textContent, /Refresh players/);

    let rejectedPlayerBody = {};
    context.fetch = async (_path, options) => {
        rejectedPlayerBody = JSON.parse(options.body);
        return {
            ok: true,
            status: 200,
            json: async () => ({
                ok: false,
                accepted: false,
                replies: ['ERROR player no longer exists'],
                error: 'ERROR player no longer exists',
                action: 'kill',
                pid: '7',
            }),
        };
    };
    await vm.runInContext(`
        openActionSheet('7', 'Alice', 6);
        playerAction('kill');
    `, context);

    assert.deepStrictEqual(rejectedPlayerBody, {
        pid: '7',
        name: 'Alice',
        revision: 6,
        action: 'kill',
    });
    assert(
        !elements.get('action-sheet').classList.contains('hidden'),
        'a rejected player action must keep its action sheet open',
    );
    assert.strictEqual(result.dataset.outcome, 'rejected');
    assert.match(result.textContent, /player no longer exists/);

    context.fetch = async () => ({
        ok: true,
        status: 200,
        json: async () => ({
            ok: true,
            accepted: true,
            replies: ['OK'],
            action: 'kill',
            pid: '7',
        }),
    });
    await vm.runInContext(`playerAction('kill')`, context);
    assert(
        elements.get('action-sheet').classList.contains('hidden'),
        'an accepted player action should close its action sheet',
    );

    elements.get('chat-input').value = 'status check';
    context.fetch = async () => ({
        ok: true,
        status: 200,
        json: async () => ({
            ok: false,
            accepted: false,
            replies: ['ERROR chat disabled'],
            error: 'ERROR chat disabled',
        }),
    });
    await vm.runInContext(`sendChat()`, context);

    assert.strictEqual(result.dataset.outcome, 'rejected');
    assert.match(result.textContent, /chat disabled/);

    vm.runInContext(`
        renderMaps([
            {
                queue_index: 4,
                filename: 'CP08.BMS',
                display_name: 'Checkpoint',
                is_current: true,
                revision: 3,
            },
            {
                queue_index: 9,
                filename: 'CP08.BMS',
                display_name: 'Checkpoint',
                is_current: false,
                revision: 3,
            },
        ]);
    `, context);
    const renderedMaps = elements.get('map-list').innerHTML;
    assert.match(
        renderedMaps,
        /data-queue-index="9"/,
        'the second duplicate row must render its own retail queue identity',
    );
    assert.match(renderedMaps, /data-mission-revision="3"/);
    assert.match(renderedMaps, /<span class="map-index">9<\/span>/);

    context.fetch = async () => {
        throw new Error('map request failed');
    };
    await vm.runInContext(`switchMap(9, 'CP08.BMS', 3)`, context);

    assert.strictEqual(result.dataset.outcome, 'error');
    assert.match(result.textContent, /map request failed/);

    let requestPath = '';
    let requestBody = {};
    context.fetch = async (path, options) => {
        requestPath = path;
        requestBody = JSON.parse(options.body);
        return {
            ok: true,
            status: 200,
            json: async () => ({
                ok: true,
                accepted: true,
                replies: ['OK'],
                action: 'next_map',
            }),
        };
    };
    await vm.runInContext(`switchMap(9, 'CP08.BMS', 3)`, context);
    assert.strictEqual(requestPath, '/api/map/switch');
    assert.deepStrictEqual(
        requestBody,
        { index: 9, map: 'CP08.BMS', revision: 3 },
        'map requests must preserve the selected queue identity',
    );

    await vm.runInContext(`quickAction('next_map', 'Next map')`, context);

    assert.strictEqual(requestPath, '/api/action');
    assert.deepStrictEqual(requestBody, { action: 'next_map' });
    assert.strictEqual(result.dataset.outcome, 'accepted');

    elements.get('chat-input').value = 'keep this message';
    vm.runInContext(`
        ws.readyState = WebSocket.OPEN;
        sendChat();
        handleWebSocketMessage({
            type: 'chat_result',
            accepted: false,
            replies: ['ERROR chat disabled'],
            error: 'ERROR chat disabled'
        });
    `, context);
    assert.strictEqual(elements.get('chat-input').value, 'keep this message');

    vm.runInContext(`
        sendChat();
        handleWebSocketMessage({
            type: 'chat_result',
            accepted: true,
            replies: ['OK - Chat sent.']
        });
    `, context);
    assert.strictEqual(elements.get('chat-input').value, '');

    vm.runInContext(`
        document.getElementById('chat-input').value = 'first message';
        sendChat();
        document.getElementById('chat-input').value = 'second message';
        sendChat();
        handleWebSocketMessage({
            type: 'chat_result',
            accepted: true,
            replies: ['OK - Chat sent.']
        });
    `, context);
    assert.strictEqual(elements.get('chat-input').value, 'second message');
    vm.runInContext(`
        handleWebSocketMessage({
            type: 'chat_result',
            accepted: true,
            replies: ['OK - Chat sent.']
        });
    `, context);
    assert.strictEqual(elements.get('chat-input').value, '');

    vm.runInContext(`
        handleWebSocketMessage({
            type: 'command_result',
            accepted: true,
            verified: false,
            replies: ['OK'],
            verification_error: 'readback did not confirm the mutation'
        });
    `, context);
    assert.strictEqual(result.dataset.outcome, 'rejected');
    assert.match(result.textContent, /readback did not confirm/);

    vm.runInContext(`
        handleWebSocketMessage({
            type: 'command_result',
            accepted: true,
            verified: true,
            replies: ['State readback matched']
        });
    `, context);
    assert.strictEqual(result.dataset.outcome, 'verified');
    assert.match(result.textContent, /Verified/);

    vm.runInContext(`
        handleWebSocketMessage({
            type: 'command_result',
            ok: false,
            accepted: true,
            verified: null,
            replies: ['OK']
        });
    `, context);
    assert.strictEqual(result.dataset.outcome, 'rejected');
    assert.match(result.textContent, /not independently verified/);

    context.fetch = async () => ({
        ok: false,
        status: 502,
        json: async () => ({
            ok: true,
            accepted: true,
            replies: ['stale proxy body'],
            error: 'Upstream unavailable',
        }),
    });
    await vm.runInContext(
        `quickAction('refresh_players', 'Refresh players')`,
        context,
    );
    assert.strictEqual(result.dataset.outcome, 'error');
    assert.match(result.textContent, /Upstream unavailable/);
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
