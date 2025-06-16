import os
import logging
import json
import asyncio
import re
import aiohttp
import uuid
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from enum import Enum

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class UserState(Enum):
    WAITING_FOR_TWITTER_URL = "waiting_for_twitter_url"
    WAITING_FOR_WALLET_ADDRESS = "waiting_for_wallet_address"
    WAITING_FOR_TOKEN_PREFERENCE = "waiting_for_token_preference"
    IDLE = "idle"

class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.user_states = {}
        self.user_data = {}
        self.setup_handlers()
    
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send a message when the command /start is issued."""
        user_id = update.effective_user.id
        self.user_states[user_id] = UserState.WAITING_FOR_TWITTER_URL
        self.user_data[user_id] = {}
        
        welcome_message = """
🐦 Welcome to the Twitter Analysis Bot!

Please share a Twitter URL that you'd like me to analyze.
        """
        await update.message.reply_text(welcome_message)
    
    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send a message when the command /help is issued."""
        help_text = """
🤖 Twitter Analysis Bot Help

Available commands:
/start - Start the analysis process
/help - Show this help message

How it works:
1. Use /start to begin
2. Share a Twitter URL
3. Provide your wallet address
4. Get your analysis results!
        """
        await update.message.reply_text(help_text)
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages."""
        user_message = update.message.text
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        chat_type = update.message.chat.type
        bot_username = context.bot.username
        
        # In groups, only respond if bot is mentioned or message is a reply to bot
        if chat_type in ['group', 'supergroup']:
            is_mentioned = f"@{bot_username}" in user_message if bot_username else False
            is_reply_to_bot = (update.message.reply_to_message and 
                             update.message.reply_to_message.from_user.id == context.bot.id)
            
            if not (is_mentioned or is_reply_to_bot):
                return
            
            # Clean the message by removing bot mention
            if is_mentioned:
                user_message = user_message.replace(f"@{bot_username}", "").strip()
        
        logger.info(f"Received message from {user_name}: {user_message}")
        
        current_state = self.user_states.get(user_id, UserState.IDLE)
        
        if current_state == UserState.WAITING_FOR_TWITTER_URL:
            await self.handle_twitter_url(update, user_message, user_id)
        elif current_state == UserState.WAITING_FOR_WALLET_ADDRESS:
            await self.handle_wallet_address(update, user_message, user_id)
        elif current_state == UserState.WAITING_FOR_TOKEN_PREFERENCE:
            await self.handle_token_preference(update, user_message, user_id)
        else:
            await update.message.reply_text("Please use /start to begin the Twitter analysis process.")
    
    async def handle_twitter_url(self, update: Update, url: str, user_id: int) -> None:
        """Handle Twitter URL input."""
        if not self.is_valid_twitter_url(url):
            await update.message.reply_text("❌ Please provide a valid Twitter URL (e.g., https://twitter.com/username/status/123456789)")
            return
        
        self.user_data[user_id]['twitter_url'] = url
        self.user_states[user_id] = UserState.WAITING_FOR_WALLET_ADDRESS
        
        await update.message.reply_text("✅ Twitter URL received!\n\nNow please provide your wallet address:")
    
    async def handle_wallet_address(self, update: Update, wallet_address: str, user_id: int) -> None:
        """Handle wallet address input and show score."""
        if not wallet_address.strip():
            await update.message.reply_text("❌ Please provide a valid wallet address.")
            return
        
        self.user_data[user_id]['wallet_address'] = wallet_address
        
        await update.message.reply_text("🔄 Processing your request...")
        
        twitter_url = self.user_data[user_id]['twitter_url']
        result = await self.analyze_twitter(twitter_url, wallet_address)
        
        self.user_data[user_id]['score'] = result['score']
        self.user_states[user_id] = UserState.WAITING_FOR_TOKEN_PREFERENCE
        
        # Calculate prize amount (score * 10)
        prize_amount = result['score'] * 10
        
        score_message = f"""
✅ **Analysis Complete!**

🔗 **Twitter URL:** {twitter_url}
💰 **Wallet Address:** {wallet_address}
📊 **Score:** {result['score']}/10
🎉 **You have won {prize_amount} USDC!**

💰 **How would you like to receive your tokens?**
You can specify percentages for different tokens:
- USDC
- ETH
- NATIVE (default)

Example: "70% USDC and 30% NATIVE" or just "NATIVE" for 100% native tokens.
        """
        
        await update.message.reply_text(score_message, parse_mode='Markdown')
    
    async def handle_token_preference(self, update: Update, preference: str, user_id: int) -> None:
        """Handle token preference input and process transaction."""
        try:
            token_allocation = self.parse_token_preference(preference)
            
            await update.message.reply_text("🔄 Processing your token allocation...")
            
            # Simulate processing time
            await asyncio.sleep(3)
            
            # Get user data
            twitter_url = self.user_data[user_id]['twitter_url']
            wallet_address = self.user_data[user_id]['wallet_address']
            score = self.user_data[user_id]['score']
            
            # Simulate transaction
            result = await self.process_token_transaction(wallet_address, token_allocation, score)
            
            # Format allocation display
            allocation_text = self.format_token_allocation(token_allocation)
            
            prize_amount = score * 10
            
            response_message = f"""
✅ **Transaction Complete!**

📊 **Score:** {score}/10
🎉 **Prize:** {prize_amount} USDC
💰 **Token Allocation:** {allocation_text}
🔗 **Transaction Hash:** {result['transaction_hash']}
📝 **Status:** {result['status']}
            """
            
            await update.message.reply_text(response_message, parse_mode='Markdown')
            
            # Clean up user data
            self.user_states[user_id] = UserState.IDLE
            del self.user_data[user_id]
            
        except ValueError as e:
            await update.message.reply_text(f"❌ {str(e)}\n\nPlease try again with a format like: '70% USDC and 30% NATIVE'")
    
    def parse_token_preference(self, preference: str) -> dict:
        """Parse token preference string into allocation dictionary."""
        preference = preference.upper().strip()
        
        # Default to 100% NATIVE if just token name is provided
        if preference in ['NATIVE', 'USDC', 'ETH']:
            return {preference: 100}
        
        # Parse percentage allocations
        token_allocation = {}
        total_percentage = 0
        
        # Find all percentage patterns like "70% USDC"
        percentage_pattern = r'(\d+)%?\s+(USDC|ETH|NATIVE)'
        matches = re.findall(percentage_pattern, preference)
        
        if not matches:
            # Try to parse simple format like "USDC 70, NATIVE 30"
            simple_pattern = r'(USDC|ETH|NATIVE)\s+(\d+)'
            matches = [(match[1], match[0]) for match in re.findall(simple_pattern, preference)]
        
        if not matches:
            raise ValueError("Could not parse token allocation. Please use format like '70% USDC and 30% NATIVE'")
        
        for percentage, token in matches:
            percentage = int(percentage)
            if token in token_allocation:
                token_allocation[token] += percentage
            else:
                token_allocation[token] = percentage
            total_percentage += percentage
        
        # Validate total percentage
        if total_percentage > 100:
            raise ValueError(f"Total percentage ({total_percentage}%) cannot exceed 100%")
        
        # If less than 100%, add remainder to NATIVE
        if total_percentage < 100:
            remainder = 100 - total_percentage
            if 'NATIVE' in token_allocation:
                token_allocation['NATIVE'] += remainder
            else:
                token_allocation['NATIVE'] = remainder
        
        return token_allocation
    
    def format_token_allocation(self, allocation: dict) -> str:
        """Format token allocation for display."""
        parts = []
        for token, percentage in allocation.items():
            parts.append(f"{percentage}% {token}")
        return ", ".join(parts)
    
    async def process_token_transaction(self, wallet_address: str, allocation: dict, score: int) -> dict:
        """Simulate token transaction processing."""
        await asyncio.sleep(2)
        
        # Generate mock transaction hash
        transaction_hash = f"0x{''.join([f'{i:02x}' for i in range(32)])}"
        
        return {
            "transaction_hash": transaction_hash,
            "status": "Transaction completed successfully"
        }
    
    def is_valid_twitter_url(self, url: str) -> bool:
        """Validate if the URL is a Twitter URL."""
        twitter_domains = ['twitter.com', 'x.com']
        return any(domain in url.lower() for domain in twitter_domains) and url.startswith(('http://', 'https://'))
    
    async def analyze_twitter(self, twitter_url: str, wallet_address: str) -> dict:
        """Analyze Twitter URL using Bitte AI API and return score."""
        try:
            # Generate unique chat ID
            chat_id = str(uuid.uuid4()).replace('-', '')[:16]
            
            # Prepare the request payload
            payload = {
                "id": chat_id,
                "messages": [{
                    "id": str(uuid.uuid4()).replace('-', '')[:16],
                    "createdAt": "2025-06-16T06:39:34.498Z",
                    "role": "user",
                    "content": twitter_url,
                    "parts": [{
                        "type": "text",
                        "text": twitter_url
                    }]
                }],
                "config": {
                    "mode": "debug",
                    "agentId": "agent-rating.vercel.app",
                    "mcpServerUrl": "https://mcp.bitte.ai/sse"
                },
                "nearWalletId": "",
                "accountId": "",
                "suiAddress": ""
            }
            
            headers = {
                'accept': '*/*',
                'authorization': f'Bearer {os.getenv("BITTE_API_KEY", "")}',
                'content-type': 'application/json',
                'origin': 'https://bitte.ai',
                'referer': f'https://bitte.ai/chat/{chat_id}?agentid=agent-rating.vercel.app&mode=debug',
                'user-agent': 'Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    os.getenv("CHAT_API_URL", "https://ai-runtime-446257178793.europe-west1.run.app/chat"),
                    json=payload,
                    headers=headers
                ) as response:
                    if response.status != 200:
                        logger.error(f"API request failed with status {response.status}")
                        return {"score": 1}
                    
                    # Parse streaming response
                    score = await self.parse_streaming_response(response)
                    return {"score": score}
                    
        except Exception as e:
            logger.error(f"Error analyzing Twitter URL: {str(e)}")
            return {"score": 1}
    
    async def parse_streaming_response(self, response) -> int:
        """Parse the streaming response from Bitte AI API."""
        try:
            content = await response.text()
            logger.info(f"Raw API response: {content}")
            
            # Parse the streaming format
            lines = content.strip().split('\n')
            score = 1  # default score
            
            for line in lines:
                try:
                    # Look for result data in the streaming format
                    if line.startswith('a:'):
                        # Parse the JSON after 'a:'
                        json_data = json.loads(line[2:])
                        if 'result' in json_data and 'data' in json_data['result']:
                            result_data = json_data['result']['data']
                            # If result_data is a string number, convert it
                            if isinstance(result_data, str) and result_data.isdigit():
                                score = int(result_data)
                            elif isinstance(result_data, (int, float)):
                                score = int(result_data)
                    elif line.startswith('0:'):
                        # Sometimes the score comes in this format
                        score_str = line[2:].strip('"')
                        if score_str.isdigit():
                            score = int(score_str)
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.debug(f"Skipping line parsing: {str(e)}")
                    continue
            
            logger.info(f"Parsed score: {score}")
            return score
            
        except Exception as e:
            logger.error(f"Error parsing streaming response: {str(e)}")
            return 1
    
    async def send_message(self, chat_id: int, message: str) -> None:
        """Send a message to a specific chat."""
        await self.application.bot.send_message(chat_id=chat_id, text=message)
    
    def run(self):
        """Start the bot."""
        logger.info("Starting bot...")
        self.application.run_polling()

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
    
    bot = TelegramBot(token)
    bot.run()

if __name__ == '__main__':
    main()