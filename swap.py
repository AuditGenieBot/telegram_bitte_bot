import os
import json
import requests
from web3 import Web3
import time
import uuid

# Configuration
CHAT_API     = os.getenv("CHAT_API_URL", "https://ai-runtime-446257178793.europe-west1.run.app/chat")
SESSION_ID   = str(uuid.uuid4())  # Generate unique session ID
PRIVATE_KEY  = os.getenv("PRIVATE_KEY")   # Set this in your environment
RPC_URL      = "https://arb1.arbitrum.io/rpc"  # Arbitrum mainnet
CHAIN_ID     = 42161
BITTE_API_KEY = os.getenv("BITTE_API_KEY")
if not BITTE_API_KEY:
    raise ValueError("BITTE_API_KEY environment variable is required")

# Initialize Web3 if private key is available
if PRIVATE_KEY:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    account = w3.eth.account.from_key(PRIVATE_KEY)
    print(f"🔑 Connected with address: {account.address}")
else:
    print("⚠️  No PRIVATE_KEY found in environment - transaction features disabled")
    w3 = None
    account = None

# Chat history buffer
chat_history = []

def send_chat_message(text):
    """Send a message to the chat API and return the full response data."""
    # Add user message to history
    user_msg = {
        "id": str(uuid.uuid4()),
        "role": "user", 
        "content": text,
        "toolInvocations": [],
        "annotations": [{"agentId": "near-cow-agent.vercel.app"}],
        "parts": [{"type": "text", "text": text}]
    }
    chat_history.append(user_msg)
    
    # Prepare payload in the same format as curl commands
    payload = {
        "id": SESSION_ID,
        "messages": chat_history,
        "config": {
            "mode": "debug",
            "agentId": "bitte-defi",
            "mcpServerUrl": "https://mcp.bitte.ai/sse"
        },
        "nearWalletId": "",
        "accountId": "",
        "evmAddress": account.address if account else os.getenv("FALLBACK_WALLET_ADDRESS", "0x293D3a1D4261570Bf30F0670cD41B5200Dc0A08f"),
        "suiAddress": ""
    }
    
    headers = {
        'accept': '*/*',
        'accept-language': 'en-GB,en-US;q=0.9,en;q=0.8',
        'authorization': f'Bearer {BITTE_API_KEY}',
        'content-type': 'application/json',
        'origin': 'https://bitte.ai',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
    }
    
    try:
        print("🔄 Sending request to BITTE AI...")
        res = requests.post(CHAT_API, json=payload, headers=headers, timeout=60)
        res.raise_for_status()
        
        # Parse the streaming response
        response_data = parse_streaming_response(res.text)
        
        # Add assistant message to history if we got one
        if response_data.get("assistant_message"):
            chat_history.append(response_data["assistant_message"])
        
        return response_data
        
    except requests.exceptions.RequestException as e:
        print(f"❌ API request failed: {e}")
        if hasattr(e.response, 'text'):
            print(f"Response: {e.response.text}")
        return {"error": str(e)}
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return {"error": str(e)}

def parse_streaming_response(response_text):
    """Parse the streaming response format to extract meaningful data."""
    lines = response_text.strip().split('\n')
    
    assistant_content = ""
    tool_calls = []
    tool_results = {}
    assistant_message = None
    
    print("🔍 Parsing streaming response...")
    
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
                # Tool calls - this is what we need!
                try:
                    tool_call_data = json.loads(line[2:])
                    print(f"🛠️  Found tool call: {tool_call_data}")
                    tool_calls.append(tool_call_data)
                except json.JSONDecodeError as e:
                    print(f"❌ Failed to parse tool call: {e}")
                    
            elif line.startswith('a:'):
                # Tool results
                try:
                    tool_result_data = json.loads(line[2:])
                    tool_call_id = tool_result_data.get('toolCallId')
                    if tool_call_id:
                        tool_results[tool_call_id] = tool_result_data
                        print(f"📋 Tool result for {tool_call_id}: {tool_result_data.get('result', {}).get('data', {}).get('type', 'unknown')}")
                except json.JSONDecodeError:
                    continue
                    
        except Exception as e:
            print(f"⚠️  Error parsing line: {e}")
            continue
    
    # Combine tool calls with their results
    combined_tools = []
    for tool_call in tool_calls:
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
    
    print(f"✅ Parsed {len(combined_tools)} tool invocation(s)")
    
    return {
        "assistant_message": assistant_message,
        "content": assistant_content,
        "toolInvocations": combined_tools
    }

def handle_tool_invocations(invocations):
    """Process tool invocations, particularly for CoWSwap transactions."""
    if not invocations:
        return
    
    print(f"🔧 Processing {len(invocations)} tool invocation(s)...")
    
    # Look for swap and transaction generation tools
    swap_data = None
    tx_data = None
    
    for inv in invocations:
        tool_name = inv.get('toolName', 'unknown')
        
        if tool_name == "swap":
            print("💱 CoWSwap transaction detected")
            result = inv.get('result', {})
            if 'data' in result:
                swap_data = result['data']
                print("📊 Swap details found")
                
        elif tool_name == "generate-evm-tx":
            print("📝 EVM transaction generation detected")
            result = inv.get('result', {})
            if 'data' in result and 'evmSignRequest' in result['data']:
                tx_data = result['data']['evmSignRequest']
                print("🔐 Transaction data extracted")
    
    # If we have transaction data, execute it automatically
    if tx_data and tx_data.get('params'):
        print("\n🚀 Auto-executing transaction...")
        execute_transaction(tx_data['params'][0], swap_data)
    elif swap_data:
        print("⚠️  Swap data found but no transaction to execute")
        print(f"Swap info: {json.dumps(swap_data.get('data', {}), indent=2)}")
    else:
        print("❌ No executable transaction found in tool results")

def execute_transaction(tx_params, swap_data=None):
    """Execute the transaction automatically."""
    if not w3 or not account:
        print("❌ Cannot execute transactions - no wallet configured")
        return
    
    print(f"\n🔐 Executing transaction automatically...")
    print(f"   To: {tx_params.get('to', 'unknown')}")
    print(f"   Value: {tx_params.get('value', '0x0')}")
    print(f"   Data: {tx_params.get('data', '')[:50]}...")
    
    if swap_data and 'data' in swap_data:
        swap_info = swap_data['data']
        if 'tokenIn' in swap_info and 'tokenOut' in swap_info:
            token_in = swap_info['tokenIn']
            token_out = swap_info['tokenOut']
            print(f"   Swapping: {token_in.get('amount', 'N/A')} → {token_out.get('amount', 'N/A')}")
            print(f"   Fee: {swap_info.get('fee', 'N/A')}")
    
    try:
        # Prepare transaction
        tx_dict = {
            "to": tx_params["to"],
            "value": int(tx_params.get("value", "0x0"), 16),
            "data": tx_params.get("data", ""),
            "nonce": w3.eth.get_transaction_count(account.address),
            "chainId": CHAIN_ID,
        }
        
        # Estimate gas
        try:
            gas_estimate = w3.eth.estimate_gas({
                **tx_dict,
                "from": account.address
            })
            print("gas estimate")
            print(gas_estimate)
            tx_dict["gas"] = gas_estimate
            print(f"⛽ Estimated gas: {gas_estimate}")
        except Exception as e:
            print(f"⚠️  Gas estimation failed: {e}")
            tx_dict["gas"] = 200000  # Default fallback
        
        # Get gas price
        tx_dict["gasPrice"] = w3.eth.gas_price
        print(f"💰 Gas price: {w3.from_wei(tx_dict['gasPrice'], 'gwei')} gwei")
        
        print("🔏 Signing transaction...")
        signed = account.sign_transaction(tx_dict)
        
        print("📡 Broadcasting transaction...")
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()
        print(f"✅ Transaction sent: {tx_hash_hex}")
        
        print("⏳ Waiting for confirmation...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        print(f"🎉 Transaction confirmed in block {receipt.blockNumber}")
        print(f"🔗 Arbitrum explorer: https://arbiscan.io/tx/{receipt.transactionHash.hex()}")
        
        # Send success notification back to the chat
        success_msg = f"✅ Swap executed successfully! Tx: {tx_hash_hex}"
        send_notification_to_chat(success_msg, tx_hash_hex)
        
        return True
        
    except Exception as e:
        print(f"❌ Transaction failed: {e}")
        send_notification_to_chat(f"❌ Transaction failed: {str(e)}")
        return False

def send_notification_to_chat(message, tx_hash=None):
    """Send a system notification back to the chat about transaction status."""
    try:
        notification_data = {
            "status": "success" if tx_hash else "error",
            "message": message
        }
        if tx_hash:
            notification_data["txHash"] = tx_hash
        
        print(f"📨 {message}")
        
    except Exception as e:
        print(f"⚠️  Failed to send notification: {e}")

def print_banner():
    """Print welcome banner."""
    print("=" * 60)
    print("🤖 BITTE.AI COWSWAP TERMINAL - AUTO EXECUTION MODE")
    print("=" * 60)
    print(f"Session ID: {SESSION_ID}")
    if account:
        print(f"Wallet: {account.address}")
    print("\nCommands:")
    print("  /help    - Show this help")
    print("  /clear   - Clear chat history")
    print("  /exit    - Exit the chat")
    print("  /wallet  - Show wallet info")
    print("\nExample commands:")
    print("  'I want to swap 0.3 USDC to WETH on Arbitrum'")
    print("  'Swap 1 USDC to ETH'")
    print("  'do it' (after getting swap quote)")
    print("=" * 60)
    print()

def show_wallet_info():
    """Display wallet information."""
    if not account:
        print("❌ No wallet connected")
        return
    
    print(f"👛 Wallet Address: {account.address}")
    try:
        balance = w3.eth.get_balance(account.address)
        eth_balance = w3.from_wei(balance, 'ether')
        print(f"💰 ETH Balance: {eth_balance:.6f} ETH")
        print(f"🔗 Chain ID: {CHAIN_ID}")
        print(f"📶 RPC: {RPC_URL}")
    except Exception as e:
        print(f"❌ Error fetching balance: {e}")

def interactive_chat():
    """Run interactive terminal chat."""
    print_banner()
    
    while True:
        try:
            # Get user input
            user_input = input("\n💬 You: ").strip()
            
            # Handle commands
            if user_input.lower() == '/exit':
                print("👋 Goodbye!")
                break
            elif user_input.lower() == '/help':
                print_banner()
                continue
            elif user_input.lower() == '/clear':
                chat_history.clear()
                print("🧹 Chat history cleared!")
                continue
            elif user_input.lower() == '/wallet':
                show_wallet_info()
                continue
            elif not user_input:
                continue
            
            # Send message to bot
            print("\n🤖 Bot: ", end="", flush=True)
            response_data = send_chat_message(user_input)
            
            if 'error' in response_data:
                print(f"❌ Error: {response_data['error']}")
                continue
            
            # Display bot response
            content = response_data.get('content', '')
            if content:
                print(content)
            else:
                print("Processing...")
            
            # Handle any tool invocations automatically
            invocations = response_data.get('toolInvocations', [])
            if invocations:
                print(f"\n🔧 {len(invocations)} tool(s) detected - executing automatically...")
                handle_tool_invocations(invocations)
                
        except KeyboardInterrupt:
            print("\n\n👋 Chat interrupted. Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            continue

def quick_swap(sell_token, buy_token, sell_amount):
    """Quick swap function for programmatic use."""
    print(f"🔄 Quick swap: {sell_amount} {sell_token} → {buy_token}")
    
    # Send swap request
    response_data = send_chat_message(
        f"I want to swap {sell_amount} {sell_token} to {buy_token} on Arbitrum"
    )
    
    if 'error' in response_data:
        print(f"❌ Error: {response_data['error']}")
        return False
    
    content = response_data.get('content', '')
    if content:
        print(f"🤖 Bot: {content}")
    
    # Handle tool invocations automatically
    invocations = response_data.get('toolInvocations', [])
    if invocations:
        print(f"🔧 Executing {len(invocations)} tool(s) automatically...")
        handle_tool_invocations(invocations)
        return True
    else:
        print("❌ No tool invocations found - swap may not have been initiated")
        return False

if __name__ == "__main__":
    import sys
    
    # Check if running in quick swap mode
    if len(sys.argv) == 4:
        # Quick swap mode: python script.py USDC WETH 0.3
        sell_token, buy_token, sell_amount = sys.argv[1], sys.argv[2], sys.argv[3]
        success = quick_swap(sell_token, buy_token, sell_amount)
        sys.exit(0 if success else 1)
    else:
        # Interactive mode
        interactive_chat()