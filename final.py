import os
import logging
import json
import asyncio
import re
import aiohttp
import uuid

import telegram
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from enum import Enum
from web3 import Web3

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class UserState(Enum):
    WAITING_FOR_TWITTER_URL = "waiting_for_twitter_url"
    WAITING_FOR_WALLET_ADDRESS = "waiting_for_wallet_address"
    WAITING_FOR_SWAP_REQUEST = "waiting_for_swap_request"
    WAITING_FOR_SWAP_CONFIRMATION = "waiting_for_swap_confirmation"
    IDLE = "idle"

class TelegramBot:
    def __init__(self, token: str, bitte_api_key: str = None, private_key: str = None):
        self.token = token
        self.bitte_api_key = bitte_api_key or os.getenv("BITTE_API_KEY")
        if not self.bitte_api_key:
            raise ValueError("BITTE_API_KEY environment variable is required")
        self.application = Application.builder().token(token).build()
        self.user_states = {}
        self.user_data = {}
        self.chat_histories = {}  # Store chat history per user
        self.setup_handlers()
        
        # CowSwap configuration
        self.chat_api = os.getenv("CHAT_API_URL", "https://ai-runtime-446257178793.europe-west1.run.app/chat")
        self.rpc_url = "https://arb1.arbitrum.io/rpc"
        self.chain_id = 42161
        
        # Web3 setup for actual transactions
        self.private_key = private_key or os.getenv("PRIVATE_KEY")
        if self.private_key:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            self.account = self.w3.eth.account.from_key(self.private_key)
            logger.info(f"Connected with address: {self.account.address}")
        else:
            logger.warning("No PRIVATE_KEY found - transaction features disabled")
            self.w3 = None
            self.account = None
    
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help))
        self.application.add_handler(CommandHandler("wallet", self.show_wallet_info))
        self.application.add_handler(CommandHandler("reset", self.reset_conversation))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
    
    async def start(self, update, context: CallbackContext) -> None:
        """Send a message when the command /start is issued."""
        user_id = update.effective_user.id
        self.user_states[user_id] = UserState.WAITING_FOR_TWITTER_URL
        self.user_data[user_id] = {}
        self.chat_histories[user_id] = []  # Initialize chat history
        
        welcome_message = """
🐦 **Welcome to the Twitter Analysis & Token Swap Bot!**

This bot will:
1. 📊 Analyze your Twitter URL and give you a score
2. 🎁 Award you USDC tokens based on your score  
3. 🔄 Allow you to swap tokens to any token you prefer using CowSwap

Please share a Twitter URL that you'd like me to analyze.
        """
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    
    async def help(self, update, context: CallbackContext) -> None:
        """Send a message when the command /help is issued."""
        help_text = """
🤖 **Twitter Analysis & Swap Bot Help**

**Available commands:**
/start - Start the analysis process
/help - Show this help message
/wallet - Show wallet information
/reset - Reset the conversation

**How it works:**
1. Use /start to begin
2. Share a Twitter URL
3. Provide your wallet address  
4. Get your analysis score and USDC prize
5. Tell the bot what kind of swap you want in natural language
   - Example: "Swap 70% to WETH and keep 30% in USDC"
   - Example: "I want all of it in ETH"

**Supported tokens:** Any token available on CowSwap
**Network:** Arbitrum One
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def reset_conversation(self, update, context: CallbackContext) -> None:
        """Reset the conversation state and history."""
        user_id = update.effective_user.id
        
        if user_id in self.user_states:
            old_state = self.user_states[user_id]
            self.user_states[user_id] = UserState.IDLE
            self.chat_histories[user_id] = []
            
            await update.message.reply_text("✅ Conversation has been reset. Use /start to begin a new session.")
        else:
            await update.message.reply_text("No active conversation to reset. Use /start to begin.")
    
    async def show_wallet_info(self, update, context: CallbackContext) -> None:
        """Show wallet information if user has provided one."""
        user_id = update.effective_user.id
        
        if user_id not in self.user_data or 'wallet_address' not in self.user_data[user_id]:
            await update.message.reply_text("❌ No wallet address found. Please use /start to begin the process.")
            return
        
        wallet_address = self.user_data[user_id]['wallet_address']
        
        wallet_info = f"""
👛 **Wallet Information**

📍 **Address:** `{wallet_address}`
🌐 **Network:** Arbitrum One
🔗 **Explorer:** [View on Arbiscan](https://arbiscan.io/address/{wallet_address})
        """
        
        await update.message.reply_text(wallet_info, parse_mode='Markdown')
    
    async def handle_callback(self, update, context: CallbackContext) -> None:
        """Handle inline keyboard callbacks."""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        data = query.data
        
        if data.startswith("confirm_swap_"):
            await self.execute_bitte_swap(query, user_id)
        elif data == "cancel_swap":
            await self.cancel_swap(query, user_id)
    
    async def handle_message(self, update, context: CallbackContext) -> None:
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
        elif current_state == UserState.WAITING_FOR_SWAP_REQUEST:
            await self.handle_swap_request(update, user_message, user_id)
        elif current_state == UserState.WAITING_FOR_SWAP_CONFIRMATION:
            # Handle any follow-up messages during swap confirmation
            if user_message.lower() in ["yes", "confirm", "proceed", "do it", "execute"]:
                await self.execute_bitte_swap_from_message(update, user_id)
            elif user_message.lower() in ["no", "cancel", "stop", "abort"]:
                await self.cancel_swap_from_message(update, user_id)
            else:
                # If not a clear confirmation/cancellation, treat as a new swap request
                await self.handle_swap_request(update, user_message, user_id)
        else:
            await update.message.reply_text("Please use /start to begin the Twitter analysis process.")
    
    async def handle_twitter_url(self, update, url: str, user_id: int) -> None:
        """Handle Twitter URL input."""
        if not self.is_valid_twitter_url(url):
            await update.message.reply_text("❌ Please provide a valid Twitter URL (e.g., https://twitter.com/username/status/123456789)")
            return
        
        self.user_data[user_id]['twitter_url'] = url
        self.user_states[user_id] = UserState.WAITING_FOR_WALLET_ADDRESS
        
        await update.message.reply_text("✅ Twitter URL received!\n\nNow please provide your wallet address:")
    
    async def handle_wallet_address(self, update, wallet_address: str, user_id: int) -> None:
        """Handle wallet address input and show score."""
        if not self.is_valid_wallet_address(wallet_address.strip()):
            await update.message.reply_text("❌ Please provide a valid wallet address (42 characters starting with 0x).")
            return
        
        self.user_data[user_id]['wallet_address'] = wallet_address.strip()
        
        await update.message.reply_text("🔄 Processing your request...")
        
        twitter_url = self.user_data[user_id]['twitter_url']
        result = await self.analyze_twitter(twitter_url, wallet_address)
        
        self.user_data[user_id]['score'] = result['score']
        self.user_states[user_id] = UserState.WAITING_FOR_SWAP_REQUEST
        
        # Calculate prize amount (score * 0.1)
        prize_amount = result['score'] * 0.1
        self.user_data[user_id]['prize_amount'] = prize_amount
        
        score_message = f"""
✅ **Analysis Complete!**

🔗 **Twitter URL:** {twitter_url}
💰 **Wallet Address:** `{wallet_address}`
📊 **Score:** {result['score']}/10
🎉 **You have won {prize_amount} USDC!**

You can now tell me how you would like to swap your USDC tokens. Just describe what you want in natural language, for example:
• "Swap all to ETH"
• "Split 50/50 between USDC and WETH"
• "Keep 30% in USDC and convert the rest to WETH"
• "I want 20% in USDC, 30% in WETH, and 50% in native ETH"

What would you like to do?
        """
        
        await update.message.reply_text(score_message, parse_mode='Markdown')
    
    async def handle_swap_request(self, update, request: str, user_id: int) -> None:
        """Handle natural language swap request using BITTE AI."""
        if user_id not in self.user_data or 'prize_amount' not in self.user_data[user_id]:
            await update.message.reply_text("❌ Error: Missing prize information. Please use /start to begin again.")
            return
        
        prize_amount = self.user_data[user_id]['prize_amount']
        wallet_address = self.user_data[user_id]['wallet_address']
        
        # Process the swap request with BITTE AI
        await update.message.reply_text("🔄 Processing your swap request...")
        
        # Prepare a more specific swap request with the prize amount
        enhanced_request = f"I want to swap {prize_amount} USDC. {request}. I'm on Arbitrum."
        
        # Send the request to BITTE AI
        response_data = await self.send_to_bitte_ai(user_id, enhanced_request, wallet_address)
        
        if 'error' in response_data:
            await update.message.reply_text(f"❌ Error: {response_data['error']}\n\nPlease try again with a different request.")
            return
        
        # Extract response content
        content = response_data.get('content', '')
        if not content:
            content = "I've processed your swap request. Would you like to proceed with this transaction?"
        
        # Check for swap tool invocations
        tool_invocations = response_data.get('tool_invocations', [])
        has_swap_tool = any(inv.get('toolName') in ["swap", "generate-evm-tx"] for inv in tool_invocations)
        
        # Store the tool invocations for execution if confirmed
        self.user_data[user_id]['pending_tool_invocations'] = tool_invocations
        
        if has_swap_tool:
            # Create confirmation keyboard
            keyboard = [
                [InlineKeyboardButton("✅ Confirm Swap", callback_data=f"confirm_swap_{user_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_swap")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            self.user_states[user_id] = UserState.WAITING_FOR_SWAP_CONFIRMATION
            
            # Send the response with confirmation buttons
            await update.message.reply_text(
                f"{content}\n\nDo you want to proceed with this transaction?", 
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # No swap tool found, let the user try again
            await update.message.reply_text(
                f"{content}\n\nPlease try another swap request or be more specific.",
                parse_mode='Markdown'
            )
    
    async def execute_bitte_swap_from_message(self, update, user_id: int) -> None:
        """Execute swap based on text confirmation."""
        await update.message.reply_text("🔄 Executing swap...")
        
        result = await self.process_tool_invocations(user_id)
        
        if result.get('success'):
            tx_hash = result.get('tx_hash', 'N/A')
            success_msg = f"""
✅ **Transaction Complete!**

{result.get('message', 'Your swap has been processed successfully.')}

{f"🔗 [View on Arbiscan](https://arbiscan.io/tx/{tx_hash})" if tx_hash != 'N/A' else ''}

Thank you for using our bot!
            """
            await update.message.reply_text(success_msg, parse_mode='Markdown')
        else:
            error_msg = f"""
❌ **Transaction Failed**

{result.get('error', 'An error occurred during the swap process.')}

Please try again with a different request or contact support.
            """
            await update.message.reply_text(error_msg, parse_mode='Markdown')
        
        # Reset state
        self.cleanup_user_data(user_id)
    
    async def cancel_swap_from_message(self, update, user_id: int) -> None:
        """Cancel swap based on text cancellation."""
        await update.message.reply_text("❌ Swap cancelled. You can make another swap request or use /start to begin again.")
        self.user_states[user_id] = UserState.WAITING_FOR_SWAP_REQUEST
    
    async def execute_bitte_swap(self, query, user_id: int) -> None:
        """Execute the swap using stored tool invocations."""
        await query.edit_message_text("🔄 **Executing swap...**", parse_mode='Markdown')
        
        result = await self.process_tool_invocations(user_id)
        
        if result.get('success'):
            tx_hash = result.get('tx_hash', 'N/A')
            success_msg = f"""
✅ **Transaction Complete!**

{result.get('message', 'Your swap has been processed successfully.')}

{f"🔗 [View on Arbiscan](https://arbiscan.io/tx/{tx_hash})" if tx_hash != 'N/A' else ''}

Thank you for using our bot!
            """
            await query.edit_message_text(success_msg, parse_mode='Markdown')
        else:
            error_msg = f"""
❌ **Transaction Failed**

{result.get('error', 'An error occurred during the swap process.')}

Please try again with a different request or contact support.
            """
            await query.edit_message_text(error_msg, parse_mode='Markdown')
        
        # Reset state
        self.cleanup_user_data(user_id)
    
    async def cancel_swap(self, query, user_id: int) -> None:
        """Cancel the swap process."""
        await query.edit_message_text("❌ **Swap cancelled.**\n\nYou can make another swap request or use /start to begin again.", parse_mode='Markdown')
        self.user_states[user_id] = UserState.WAITING_FOR_SWAP_REQUEST
    
    async def process_tool_invocations(self, user_id: int) -> dict:
        """Process stored tool invocations for swap execution."""
        if user_id not in self.user_data or 'pending_tool_invocations' not in self.user_data[user_id]:
            return {"success": False, "error": "No pending swap transaction found"}
        
        tool_invocations = self.user_data[user_id]['pending_tool_invocations']
        
        # Check if we have a wallet configured
        if not self.w3 or not self.account:
            return {"success": False, "error": "No wallet configured - cannot execute transactions"}
        
        # Look for swap and transaction generation tools
        swap_data = None
        tx_data = None
        
        for inv in tool_invocations:
            tool_name = inv.get('toolName', '')
            
            if tool_name == "swap":
                result = inv.get('result', {})
                logger.info(f"Swap tool result: {result}")
                if 'data' in result:
                    swap_data = result['data']
            
            elif tool_name == "generate-evm-tx":
                result = inv.get('result', {})
                logger.info(f"EVM tx tool result: {result}")
                if 'data' in result and 'evmSignRequest' in result['data']:
                    tx_data = result['data']['evmSignRequest']
        
        # If we have transaction data, execute it
        if tx_data and tx_data.get('params'):
            try:
                tx_hash = await self.execute_transaction(tx_data['params'][0], swap_data)
                if tx_hash:
                    message = "Transaction executed successfully"
                    if swap_data and 'data' in swap_data:
                        swap_info = swap_data.get('data', {})
                        if 'tokenIn' in swap_info and 'tokenOut' in swap_info:
                            token_in = swap_info['tokenIn']
                            token_out = swap_info['tokenOut']
                            token_in_amount = token_in.get('amount', 'unknown amount')
                            token_in_symbol = token_in.get('symbol', 'unknown token')
                            token_out_amount = token_out.get('amount', 'unknown amount')
                            token_out_symbol = token_out.get('symbol', 'unknown token')
                            
                            message = f"Successfully swapped {token_in_amount} {token_in_symbol} to {token_out_amount} {token_out_symbol}"
                    
                    return {
                        "success": True,
                        "tx_hash": tx_hash,
                        "message": message
                    }
                else:
                    return {"success": False, "error": "Transaction execution failed"}
            except Exception as e:
                logger.error(f"Transaction execution error: {str(e)}")
                return {"success": False, "error": f"Transaction failed: {str(e)}"}
        elif swap_data:
            return {"success": False, "error": "Swap data found but no transaction to execute"}
        else:
            return {"success": False, "error": "No executable transaction found in tool results"}
    
    async def send_to_bitte_ai(self, user_id: int, text: str, wallet_address: str) -> dict:
        """Send a message to the BITTE AI API and process the response."""
        try:
            # Get or initialize chat history
            if user_id not in self.chat_histories:
                self.chat_histories[user_id] = []
            
            # Add user message to history
            user_msg = {
                "id": str(uuid.uuid4()),
                "role": "user", 
                "content": text,
                "toolInvocations": [],
                "annotations": [{"agentId": "bitte-defi"}],
                "parts": [{"type": "text", "text": text}]
            }
            
            self.chat_histories[user_id].append(user_msg)
            
            # Generate unique session ID if not exists
            if 'session_id' not in self.user_data.get(user_id, {}):
                self.user_data[user_id]['session_id'] = str(uuid.uuid4())
            
            session_id = self.user_data[user_id]['session_id']
            
            # Prepare payload
            payload = {
                "id": session_id,
                "messages": self.chat_histories[user_id],
                "config": {
                    "mode": "debug",
                    "agentId": "bitte-defi",
                    "mcpServerUrl": "https://mcp.bitte.ai/sse"
                },
                "nearWalletId": "",
                "accountId": "",
                "evmAddress": wallet_address,
                "suiAddress": ""
            }
            
            headers = {
                'accept': '*/*',
                'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
                'authorization': f'Bearer {self.bitte_api_key}',
                'content-type': 'application/json',
                'origin': 'https://bitte.ai',
                'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.chat_api, json=payload, headers=headers, timeout=60) as response:
                    if response.status != 200:
                        logger.error(f"BITTE AI API request failed with status {response.status}")
                        return {"error": f"API request failed: {response.status}"}
                    
                    response_text = await response.text()
                    
                    # Parse the streaming response
                    parsed_response = await self.parse_streaming_response(response_text)
                    
                    # Add assistant message to history if we got one
                    if parsed_response.get('assistant_message'):
                        self.chat_histories[user_id].append(parsed_response['assistant_message'])
                    
                    return parsed_response
                    
        except Exception as e:
            logger.error(f"Error communicating with BITTE AI: {str(e)}")
            return {"error": str(e)}
    
    async def parse_streaming_response(self, response_text: str) -> dict:
        """Parse the streaming response format from BITTE AI."""
        try:
            lines = response_text.strip().split('\n')
            
            assistant_content = ""
            tool_invocations = []
            tool_results = {}
            assistant_message = None
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    # Handle different line formats from streaming response
                    if line.startswith('0:'):
                        # Text content from bot
                        content = json.loads(line[2:])
                        if isinstance(content, str):
                            assistant_content += content
                    
                    elif line.startswith('9:'):
                        # Tool calls
                        tool_call_data = json.loads(line[2:])
                        tool_invocations.append(tool_call_data)
                    
                    elif line.startswith('a:'):
                        # Tool results
                        tool_result_data = json.loads(line[2:])
                        tool_call_id = tool_result_data.get('toolCallId')
                        if tool_call_id:
                            tool_results[tool_call_id] = tool_result_data
                
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"Error parsing line: {str(e)}")
                    continue
            
            # Combine tool calls with their results
            combined_tools = []
            for tool_call in tool_invocations:
                tool_call_id = tool_call.get('toolCallId')
                if tool_call_id in tool_results:
                    combined_tool = {
                        **tool_call,
                        'result': tool_results[tool_call_id].get('result', {}),
                        'state': 'completed'
                    }
                else:
                    combined_tool = {
                        **tool_call,
                        'state': 'pending'
                    }
                combined_tools.append(combined_tool)
            
            # Create assistant message
            if assistant_content or combined_tools:
                assistant_message = {
                    "id": f"msg-{str(uuid.uuid4())}",
                    "role": "assistant",
                    "content": assistant_content,
                    "parts": [{"type": "text", "text": assistant_content}],
                    "toolInvocations": combined_tools,
                    "annotations": [{"agentId": "bitte-defi"}]
                }
            
            return {
                "assistant_message": assistant_message,
                "content": assistant_content,
                "tool_invocations": combined_tools
            }
            
        except Exception as e:
            logger.error(f"Error parsing streaming response: {str(e)}")
            return {"error": f"Failed to parse response: {str(e)}"}
    
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
                'authorization': f'Bearer {os.getenv("TWITTER_ANALYSIS_API_KEY", "")}',
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
                    score = await self.parse_twitter_scoring_response(response)
                    return {"score": score}
                    
        except Exception as e:
            logger.error(f"Error analyzing Twitter URL: {str(e)}")
            return {"score": 1}
    
    async def parse_twitter_scoring_response(self, response) -> int:
        """Parse the streaming response from Twitter scoring API."""
        try:
            content = await response.text()
            
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
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
            
            return score
            
        except Exception as e:
            logger.error(f"Error parsing Twitter scoring response: {str(e)}")
            return 1
    
    def is_valid_twitter_url(self, url: str) -> bool:
        """Validate if the URL is a Twitter URL."""
        twitter_domains = ['twitter.com', 'x.com']
        return any(domain in url.lower() for domain in twitter_domains) and url.startswith(('http://', 'https://'))
    
    def is_valid_wallet_address(self, address: str) -> bool:
        """Validate if the address is a valid Ethereum wallet address."""
        return len(address) == 42 and address.startswith('0x') and all(c in '0123456789abcdefABCDEF' for c in address[2:])
    
    async def execute_transaction(self, tx_params: dict, swap_data: dict = None) -> str:
        """Execute the transaction using web3."""
        logger.info(f"Executing transaction to: {tx_params.get('to', 'unknown')}")
        logger.info(f"Value: {tx_params.get('value', '0x0')}")
        logger.info(f"Data: {tx_params.get('data', '')[:50]}...")
        
        if swap_data and 'data' in swap_data:
            swap_info = swap_data['data']
            if 'tokenIn' in swap_info and 'tokenOut' in swap_info:
                token_in = swap_info['tokenIn']
                token_out = swap_info['tokenOut']
                logger.info(f"Swapping: {token_in.get('amount', 'N/A')} {token_in.get('symbol', 'N/A')} → {token_out.get('amount', 'N/A')} {token_out.get('symbol', 'N/A')}")
                logger.info(f"Fee: {swap_info.get('fee', 'N/A')}")
        
        try:
            # Prepare transaction
            tx_dict = {
                "to": tx_params["to"],
                "value": int(tx_params.get("value", "0x0"), 16),
                "data": tx_params.get("data", ""),
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "chainId": self.chain_id,
            }
            
            # Estimate gas
            try:
                gas_estimate = self.w3.eth.estimate_gas({
                    **tx_dict,
                    "from": self.account.address
                })
                tx_dict["gas"] = gas_estimate
                logger.info(f"Estimated gas: {gas_estimate}")
            except Exception as e:
                logger.warning(f"Gas estimation failed: {e}")
                tx_dict["gas"] = 200000  # Default fallback
            
            # Get gas price
            tx_dict["gasPrice"] = self.w3.eth.gas_price
            logger.info(f"Gas price: {self.w3.from_wei(tx_dict['gasPrice'], 'gwei')} gwei")
            
            # Sign transaction
            logger.info("Signing transaction...")
            signed = self.account.sign_transaction(tx_dict)
            
            # Broadcast transaction
            logger.info("Broadcasting transaction...")
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash_hex = tx_hash.hex()
            logger.info(f"Transaction sent: {tx_hash_hex}")
            
            # Wait for confirmation
            logger.info("Waiting for confirmation...")
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            logger.info(f"Transaction confirmed in block {receipt.blockNumber}")
            logger.info(f"Arbitrum explorer: https://arbiscan.io/tx/{receipt.transactionHash.hex()}")
            
            return tx_hash_hex
            
        except Exception as e:
            logger.error(f"Transaction failed: {e}")
            raise e
    
    def cleanup_user_data(self, user_id: int) -> None:
        """Clean up user data after transaction completion."""
        self.user_states[user_id] = UserState.IDLE
        
        # Keep wallet address but clear other transaction data
        if user_id in self.user_data:
            wallet_address = self.user_data[user_id].get('wallet_address')
            session_id = self.user_data[user_id].get('session_id')
            self.user_data[user_id] = {}
            
            if wallet_address:
                self.user_data[user_id]['wallet_address'] = wallet_address
            
            if session_id:
                self.user_data[user_id]['session_id'] = session_id
    
    def run(self):
        """Start the bot."""
        logger.info("Starting Twitter Analysis & CowSwap Bot...")
        self.application.run_polling()

def main():
    # Configuration
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
    
    bitte_api_key = os.getenv("BITTE_API_KEY")
    if not bitte_api_key:
        raise ValueError("BITTE_API_KEY environment variable is required")
    
    private_key = os.getenv("PRIVATE_KEY")  # Set this in your environment
    
    bot = TelegramBot(telegram_token, bitte_api_key, private_key)
    bot.run()

if __name__ == '__main__':
    main()