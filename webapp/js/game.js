// Bingo Game Client
class BingoGame {
    constructor(config) {
        this.config = {
            gameId: null,
            userId: null,
            username: null,
            roomId: 'classic',
            cardPrice: 10,
            ...config
        };
        
        this.state = {
            status: 'waiting',
            card: [],
            marked: new Set(),
            calledNumbers: [],
            players: 0,
            prizePool: 0,
            winners: []
        };
        
        this.ws = null;
        this.ui = null;
        this.autoMark = true;
    }

    init() {
        // Initialize WebSocket
        this.ws = new BingoWebSocket(this.config.roomId, this.config.userId);
        
        // Setup WebSocket handlers
        this.ws.on('game_state', (data) => this.onGameState(data));
        this.ws.on('player_joined', (data) => this.onPlayerJoined(data));
        this.ws.on('number_marked', (data) => this.onNumberMarked(data));
        this.ws.on('game_finished', (data) => this.onGameFinished(data));
        
        // Connect
        this.ws.connect();
        
        // Load card selection UI
        this.showCardSelection();
    }

    async showCardSelection() {
        const cards = await this.fetchAvailableCards();
        
        // Show card grid for selection
        const cardGrid = document.getElementById('card-selection');
        if (!cardGrid) return;
        
        cardGrid.innerHTML = '';
        
        for (let i = 1; i <= 400; i++) {
            const cardBtn = document.createElement('button');
            cardBtn.className = 'card-btn';
            cardBtn.textContent = `Card #${i}`;
            cardBtn.onclick = () => this.selectCard(i);
            
            // Check if card is taken (would come from server)
            if (cards.taken.includes(i)) {
                cardBtn.disabled = true;
                cardBtn.classList.add('taken');
            }
            
            cardGrid.appendChild(cardBtn);
        }
    }

    async selectCard(cardNumber) {
        // Call API to join game
        const response = await fetch('/api/game/join', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: this.config.userId,
                username: this.config.username,
                room_id: this.config.roomId,
                card_number: cardNumber
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            // Hide selection, show game board
            document.getElementById('card-selection').style.display = 'none';
            document.getElementById('game-board').style.display = 'block';
            
            // Initialize board with card
            this.renderBoard(result.game_state);
        } else {
            alert(result.message);
        }
    }

    renderBoard(gameState) {
        if (!gameState || !gameState.player) return;
        
        this.state.card = gameState.player.card;
        this.state.marked = new Set(gameState.player.marked || []);
        this.state.prizePool = gameState.prize_pool;
        this.state.players = gameState.players;
        
        // Create bingo grid
        const grid = document.getElementById('bingo-grid');
        if (!grid) return;
        
        grid.innerHTML = '';
        
        // Add BINGO header
        const header = ['B', 'I', 'N', 'G', 'O'];
        header.forEach(letter => {
            const cell = document.createElement('div');
            cell.className = 'grid-cell header';
            cell.textContent = letter;
            grid.appendChild(cell);
        });
        
        // Add numbers (5x5 grid)
        for (let row = 0; row < 5; row++) {
            for (let col = 0; col < 5; col++) {
                const index = row * 5 + col;
                const number = this.state.card[index];
                
                const cell = document.createElement('div');
                cell.className = 'grid-cell';
                if (this.state.marked.has(number)) {
                    cell.classList.add('marked');
                }
                
                cell.textContent = number;
                cell.onclick = () => this.markNumber(number);
                
                grid.appendChild(cell);
            }
        }
        
        // Update UI
        this.updateUI();
    }

    markNumber(number) {
        if (this.state.status !== 'active') {
            alert('Game not active');
            return;
        }
        
        if (this.state.marked.has(number)) {
            return;
        }
        
        // Send to server
        this.ws.send('mark', { number });
        
        // Optimistic update
        this.state.marked.add(number);
        this.updateCellMarked(number);
        
        // Check for bingo locally
        if (this.checkBingo()) {
            this.callBingo();
        }
    }

    updateCellMarked(number) {
        const cells = document.querySelectorAll('.grid-cell');
        cells.forEach(cell => {
            if (cell.textContent == number) {
                cell.classList.add('marked');
            }
        });
    }

    checkBingo() {
        // Check all numbers marked
        return this.state.card.every(num => this.state.marked.has(num));
    }

    callBingo() {
        if (confirm('Do you have BINGO?')) {
            this.ws.send('bingo', {});
        }
    }

    onGameState(data) {
        this.state.calledNumbers = data.called_numbers || [];
        this.state.players = data.players;
        this.state.prizePool = data.prize_pool;
        this.state.winners = data.winners || [];
        this.state.status = data.status;
        
        this.updateUI();
        
        // Update called numbers display
        this.renderCalledNumbers();
    }

    onPlayerJoined(data) {
        this.state.players = data.player_count;
        this.updateUI();
        
        // Show notification
        this.showNotification(`${data.username} joined the game`);
    }

    onNumberMarked(data) {
        // Other player marked a number
        this.showNotification(`Player marked number ${data.number}`);
    }

    onGameFinished(data) {
        if (data.winners.includes(this.config.userId)) {
            alert(`🎉 YOU WON ${data.prize} Birr! 🎉`);
        } else {
            alert(`Game finished! Winner: ${data.winners[0]}`);
        }
        
        // Show replay option
        setTimeout(() => {
            if (confirm('Play again?')) {
                location.reload();
            }
        }, 3000);
    }

    renderCalledNumbers() {
        const container = document.getElementById('called-numbers');
        if (!container) return;
        
        container.innerHTML = '';
        
        this.state.calledNumbers.slice(-10).forEach(number => {
            const ball = document.createElement('div');
            ball.className = 'number-ball';
            
            // Color by range
            if (number <= 15) ball.classList.add('b-range');
            else if (number <= 30) ball.classList.add('i-range');
            else if (number <= 45) ball.classList.add('n-range');
            else if (number <= 60) ball.classList.add('g-range');
            else ball.classList.add('o-range');
            
            ball.textContent = number;
            container.appendChild(ball);
        });
    }

    updateUI() {
        // Update player count
        const playerCount = document.getElementById('player-count');
        if (playerCount) {
            playerCount.textContent = this.state.players;
        }
        
        // Update prize pool
        const prizePool = document.getElementById('prize-pool');
        if (prizePool) {
            prizePool.textContent = this.state.prizePool;
        }
        
        // Update game status
        const status = document.getElementById('game-status');
        if (status) {
            status.textContent = this.state.status.toUpperCase();
        }
        
        // Update winners
        if (this.state.winners.length > 0) {
            const winnerDisplay = document.getElementById('winners');
            if (winnerDisplay) {
                winnerDisplay.textContent = `Winners: ${this.state.winners.join(', ')}`;
            }
        }
    }

    showNotification(message) {
        const notif = document.createElement('div');
        notif.className = 'notification';
        notif.textContent = message;
        
        document.body.appendChild(notif);
        
        setTimeout(() => {
            notif.remove();
        }, 3000);
    }

    async fetchAvailableCards() {
        // Fetch from server
        const response = await fetch(`/api/game/${this.config.gameId}/cards`);
        return await response.json();
    }
}

// Export
window.BingoGame = BingoGame;
