// WebSocket Manager for Bingo Game
class BingoWebSocket {
    constructor(roomId, userId) {
        this.roomId = roomId;
        this.userId = userId;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.listeners = new Map();
        this.pingInterval = null;
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/${this.roomId}/${this.userId}`;
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.startPing();
            this.emit('connected', {});
        };
        
        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (e) {
                console.error('Failed to parse message:', e);
            }
        };
        
        this.ws.onclose = () => {
            console.log('WebSocket disconnected');
            this.stopPing();
            this.reconnect();
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    handleMessage(message) {
        // Dispatch to specific listeners
        if (this.listeners.has(message.type)) {
            this.listeners.get(message.type).forEach(callback => {
                callback(message.data);
            });
        }
        
        // Handle system messages
        switch (message.type) {
            case 'game_state':
                this.updateGameState(message.data);
                break;
            case 'player_joined':
                this.updatePlayerCount(message.data);
                break;
            case 'number_marked':
                this.updatePlayerMarked(message.data);
                break;
            case 'bingo_achieved':
                this.showBingoAlert(message.data);
                break;
            case 'game_finished':
                this.handleGameFinished(message.data);
                break;
        }
    }

    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);
    }

    off(event, callback) {
        if (this.listeners.has(event)) {
            const callbacks = this.listeners.get(event);
            const index = callbacks.indexOf(callback);
            if (index !== -1) {
                callbacks.splice(index, 1);
            }
        }
    }

    send(type, data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type, ...data }));
        } else {
            console.warn('WebSocket not connected');
        }
    }

    reconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            console.log(`Reconnecting... Attempt ${this.reconnectAttempts}`);
            setTimeout(() => this.connect(), 2000 * this.reconnectAttempts);
        }
    }

    startPing() {
        this.pingInterval = setInterval(() => {
            this.send('ping', {});
        }, 30000);
    }

    stopPing() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
        }
        this.stopPing();
    }

    // UI Update Methods (override these)
    updateGameState(data) {
        console.log('Game state update:', data);
    }

    updatePlayerCount(data) {
        console.log('Player count update:', data);
    }

    updatePlayerMarked(data) {
        console.log('Player marked:', data);
    }

    showBingoAlert(data) {
        console.log('Bingo alert:', data);
    }

    handleGameFinished(data) {
        console.log('Game finished:', data);
    }
}

// Export for use
window.BingoWebSocket = BingoWebSocket;