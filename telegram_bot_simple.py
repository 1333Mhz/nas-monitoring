#!/usr/bin/env python3
"""
NAS Telegram Bot Enhanced - Integrato con servizi specializzati
Versione semplificata che usa Netdata, Scrutiny, Duplicati, Uptime Kuma
"""

import requests
import json
import os
from datetime import datetime, timedelta
import asyncio
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters
import telegram
import docker

# CONFIGURAZIONE
TOKEN = os.getenv("TELEGRAM_TOKEN", "7482241317:AAHt0GdV71rTPBMFsyGVkS_1b-q8hCuQZpM")
ALLOWED_CHAT_IDS = [int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "612838063").split(",")]

# URLs servizi (da docker-compose)
NETDATA_URL = os.getenv("NETDATA_URL", "http://nas-netdata:19999")
SCRUTINY_URL = os.getenv("SCRUTINY_URL", "http://nas-scrutiny:8086")
UPTIME_KUMA_URL = os.getenv("UPTIME_KUMA_URL", "http://nas-uptime-kuma:3001")
DUPLICATI_URL = os.getenv("DUPLICATI_URL", "http://nas-duplicati:8200")
PORTAINER_URL = os.getenv("PORTAINER_URL", "http://portainer:9000")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")

# Container critici da monitorare
CRITICAL_CONTAINERS = [
    "transmission-openvpn",
    "Immich-SERVER", 
    "vaultwarden",
    "seafile",
    "npm",  # nginx-proxy-manager
    "portainer"
]

def is_authorized_user(chat_id):
    """Verifica autorizzazione"""
    return chat_id in ALLOWED_CHAT_IDS

async def check_authorization(update, context):
    """Middleware autorizzazione"""
    if not is_authorized_user(update.effective_chat.id):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Non autorizzato"
        )
        return False
    return True

def get_netdata_metrics():
    """Ottieni metriche principali da Netdata"""
    try:
        # API Netdata per metriche sistema
        response = requests.get(f"{NETDATA_URL}/api/v1/allmetrics?format=json", timeout=10)
        if response.status_code == 200:
            data = response.json()
            
            # Estrai metriche chiave
            cpu_percent = 0
            ram_percent = 0
            temp_cpu = 0
            
            # Parse CPU usage
            if 'system.cpu' in data:
                cpu_data = data['system.cpu']['dimensions']
                if 'idle' in cpu_data:
                    idle = float(cpu_data['idle']['value'])
                    cpu_percent = 100 - idle
            
            # Parse RAM usage  
            if 'system.ram' in data:
                ram_data = data['system.ram']['dimensions']
                if 'used' in ram_data and 'free' in ram_data:
                    used = float(ram_data['used']['value'])
                    free = float(ram_data['free']['value'])
                    total = used + free
                    ram_percent = (used / total) * 100 if total > 0 else 0
            
            # Parse temperatura CPU (se disponibile)
            for key in data:
                if 'temp' in key.lower() and 'cpu' in key.lower():
                    temp_data = data[key]['dimensions']
                    for temp_key in temp_data:
                        temp_cpu = float(temp_data[temp_key]['value'])
                        break
                    break
            
            return {
                "cpu_percent": round(cpu_percent, 1),
                "ram_percent": round(ram_percent, 1),
                "cpu_temp": round(temp_cpu, 1),
                "status": "ok"
            }
    except Exception as e:
        return {"error": f"Netdata API error: {e}"}

def get_scrutiny_summary():
    """Ottieni riassunto SMART da Scrutiny"""
    try:
        response = requests.get(f"{SCRUTINY_URL}/api/summary", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return {
                "disks": data.get('data', {}),
                "status": "ok"
            }
    except Exception as e:
        return {"error": f"Scrutiny API error: {e}"}

def get_docker_containers():
    """Ottieni stato container Docker"""
    try:
        client = docker.from_env()
        containers = client.containers.list(all=True)
        
        container_status = {}
        running_count = 0
        total_count = len(containers)
        
        for container in containers:
            name = container.name
            status = container.status
            
            container_status[name] = {
                "status": status,
                "is_critical": name in CRITICAL_CONTAINERS
            }
            
            if status == 'running':
                running_count += 1
        
        return {
            "containers": container_status,
            "running": running_count,
            "total": total_count,
            "status": "ok"
        }
    except Exception as e:
        return {"error": f"Docker API error: {e}"}

def get_transmission_vpn_status():
    """Controlla specificamente transmission-openvpn"""
    try:
        client = docker.from_env()
        container = client.containers.get("transmission-openvpn")
        
        # Check se container è running
        if container.status != 'running':
            return {"status": "stopped", "vpn": "unknown"}
        
        # Check logs per VPN status
        logs = container.logs(tail=50).decode('utf-8')
        
        vpn_connected = False
        if any(keyword in logs.lower() for keyword in ['initialization sequence completed', 'connected', 'tun/tap device opened']):
            vpn_connected = True
            
        return {
            "container_status": "running",
            "vpn_connected": vpn_connected,
            "status": "ok"
        }
    except Exception as e:
        return {"error": f"Transmission-VPN check error: {e}"}

def get_nas_context():
    """Contesto completo NAS per AI"""
    context = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "services": {}
    }
    
    # Netdata system metrics
    netdata = get_netdata_metrics()
    if "error" not in netdata:
        context["services"]["system"] = netdata
    
    # Scrutiny SMART data
    scrutiny = get_scrutiny_summary()
    if "error" not in scrutiny:
        context["services"]["smart"] = scrutiny
    
    # Docker containers
    docker_info = get_docker_containers()
    if "error" not in docker_info:
        context["services"]["containers"] = docker_info
    
    # Transmission VPN status
    vpn_status = get_transmission_vpn_status()
    if "error" not in vpn_status:
        context["services"]["transmission_vpn"] = vpn_status
    
    # BTRFS filesystem info
    try:
        import subprocess
        df_result = subprocess.run(['df', '-h', '/mnt/nas'], capture_output=True, text=True)
        if df_result.returncode == 0:
            lines = df_result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    context["services"]["btrfs"] = {
                        "total": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "usage_percent": parts[4]
                    }
    except Exception as e:
        context["services"]["btrfs"] = {"error": str(e)}
    
    return context

async def ollama_query(prompt, include_context=True):
    """Query Ollama con contesto NAS"""
    if include_context:
        nas_data = get_nas_context()
        context_prompt = f"""Sei l'assistente AI del NAS di Eros.

STATO NAS CORRENTE:
{json.dumps(nas_data, indent=1)}

SERVIZI MONITORING ATTIVI:
- Netdata (monitoring sistema): {NETDATA_URL}
- Scrutiny (SMART dischi): {SCRUTINY_URL}
- Uptime Kuma (service monitoring): {UPTIME_KUMA_URL}
- Duplicati (backup versioning): {DUPLICATI_URL}
- Portainer (container management): {PORTAINER_URL}

CONTAINER CRITICI DA MONITORARE:
{', '.join(CRITICAL_CONTAINERS)}

BACKUP SOURCE: /mnt/nas/docker/ (dati container)
BACKUP DEST: /mnt/nas/backup/duplicati/

DOMANDA: {prompt}

Rispondi in italiano, conciso ma completo. Usa emoji appropriati.
Analizza i dati forniti e dai consigli specifici se necessario."""
    else:
        context_prompt = prompt
    
    try:
        data = {
            "model": "llama3.2:3b",
            "prompt": context_prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
                "num_ctx": 3072
            }
        }
        
        response = requests.post(f"{OLLAMA_URL}/api/generate", json=data, timeout=180)
        response.raise_for_status()
        result = response.json()
        return result.get('response', 'Errore risposta IA.')
    except Exception as e:
        return f'❌ Errore Ollama: {e}'

# =================== COMANDI ===================

async def cmd_status(update, context):
    """Status generale NAS con tutti i servizi"""
    if not await check_authorization(update, context):
        return
        
    try:
        nas_data = get_nas_context()
        
        message = "📊 **NAS STATUS COMPLETO**\n\n"
        
        # Sistema
        if "system" in nas_data["services"]:
            sys_data = nas_data["services"]["system"]
            cpu = sys_data.get('cpu_percent', 0)
            ram = sys_data.get('ram_percent', 0)
            temp = sys_data.get('cpu_temp', 0)
            
            cpu_emoji = "🟢" if cpu < 50 else "🟡" if cpu < 80 else "🔴"
            ram_emoji = "🟢" if ram < 70 else "🟡" if ram < 90 else "🔴"
            
            message += f"🖥️ **SISTEMA:**\n"
            message += f"{cpu_emoji} CPU: {cpu}%"
            if temp > 0:
                message += f" | {temp}°C"
            message += f"\n{ram_emoji} RAM: {ram}%\n\n"
        
        # Container Docker
        if "containers" in nas_data["services"]:
            cont_data = nas_data["services"]["containers"]
            running = cont_data.get('running', 0)
            total = cont_data.get('total', 0)
            
            container_emoji = "🟢" if running == total else "🟡" if running > 0 else "🔴"
            message += f"🐳 **CONTAINER:**\n"
            message += f"{container_emoji} Attivi: {running}/{total}\n"
            
            # Check container critici
            containers = cont_data.get('containers', {})
            critical_down = []
            for name, info in containers.items():
                if info.get('is_critical') and info.get('status') != 'running':
                    critical_down.append(name)
            
            if critical_down:
                message += f"🚨 Critici offline: {', '.join(critical_down)}\n"
            message += "\n"
        
        # Transmission VPN
        if "transmission_vpn" in nas_data["services"]:
            vpn_data = nas_data["services"]["transmission_vpn"]
            container_status = vpn_data.get('container_status', 'unknown')
            vpn_connected = vpn_data.get('vpn_connected', False)
            
            if container_status == 'running' and vpn_connected:
                message += "🟢 **TRANSMISSION-VPN:** Online + VPN attiva\n"
            elif container_status == 'running':
                message += "🟡 **TRANSMISSION-VPN:** Online ma VPN offline\n"
            else:
                message += "🔴 **TRANSMISSION-VPN:** Container offline\n"
            message += "\n"
        
        # Storage BTRFS
        if "btrfs" in nas_data["services"] and "error" not in nas_data["services"]["btrfs"]:
            btrfs_data = nas_data["services"]["btrfs"]
            usage = btrfs_data.get('usage_percent', '0%').replace('%', '')
            
            try:
                usage_num = int(usage)
                storage_emoji = "🟢" if usage_num < 80 else "🟡" if usage_num < 95 else "🔴"
                message += f"💾 **STORAGE BTRFS:**\n"
                message += f"{storage_emoji} /mnt/nas: {btrfs_data.get('used', 'N/A')}/{btrfs_data.get('total', 'N/A')} ({usage}%)\n\n"
            except:
                message += f"💾 **STORAGE:** Dati non disponibili\n\n"
        
        # Link servizi
        message += "🔧 **SERVIZI WEB:**\n"
        message += f"📊 Netdata: http://192.168.1.50:19999\n"
        message += f"💾 Scrutiny: http://192.168.1.50:8086\n"
        message += f"📦 Duplicati: http://192.168.1.50:8200\n"
        message += f"⏰ Uptime Kuma: http://192.168.1.50:3001\n\n"
        
        message += f"🕐 {datetime.now().strftime('%H:%M:%S')}"
        
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message,
            parse_mode='Markdown'
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Errore status: {e}"
        )

async def cmd_vpn(update, context):
    """Check specifico VPN transmission"""
    if not await check_authorization(update, context):
        return
        
    vpn_status = get_transmission_vpn_status()
    
    if "error" in vpn_status:
        message = f"❌ Errore check VPN: {vpn_status['error']}"
    else:
        container_status = vpn_status.get('container_status', 'unknown')
        vpn_connected = vpn_status.get('vpn_connected', False)
        
        message = "🔒 **TRANSMISSION-OPENVPN STATUS**\n\n"
        
        if container_status == 'running':
            message += "✅ Container: Online\n"
        else:
            message += "❌ Container: Offline\n"
        
        if vpn_connected:
            message += "✅ VPN: Connessa\n"
        else:
            message += "❌ VPN: Disconnessa\n"
        
        # Overall status
        if container_status == 'running' and vpn_connected:
            message += "\n🟢 **STATUS: Tutto OK**"
        elif container_status == 'running':
            message += "\n🟡 **STATUS: Container OK ma VPN offline**"
        else:
            message += "\n🔴 **STATUS: Container offline**"
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message,
        parse_mode='Markdown'
    )

async def cmd_disks(update, context):
    """Status SMART dischi"""
    if not await check_authorization(update, context):
        return
        
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, 
        action=telegram.constants.ChatAction.TYPING
    )
    
    response = await ollama_query("Analizza lo stato SMART dei dischi. Come stanno sda, sdb e nvme? Ci sono problemi di temperatura o errori SMART?")
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=response
    )

async def cmd_containers(update, context):
    """Analisi container con AI"""
    if not await check_authorization(update, context):
        return
        
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, 
        action=telegram.constants.ChatAction.TYPING
    )
    
    response = await ollama_query("Analizza lo stato dei container Docker. Quanti sono attivi? Ci sono container critici offline? Come sta transmission-openvpn?")
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=response
    )

async def cmd_backup(update, context):
    """Stato backup"""
    if not await check_authorization(update, context):
        return
        
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, 
        action=telegram.constants.ChatAction.TYPING
    )
    
    response = await ollama_query("Analizza il sistema di backup. I dati in /mnt/nas/docker/ sono protetti? Come funziona il versioning con Duplicati?")
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=response
    )

async def cmd_services(update, context):
    """Lista servizi monitoring"""
    if not await check_authorization(update, context):
        return
        
    message = """🔧 **SERVIZI MONITORING NAS**

📊 **Netdata** (Sistema Real-time)
🔗 http://192.168.1.50:19999
• CPU, RAM, Temperature, Load
• Grafici real-time interattivi
• Alert automatici

💾 **Scrutiny** (SMART Dischi)
🔗 http://192.168.1.50:8086
• Test SMART automatici (weekly/monthly)
• Temperature sda, sdb, nvme
• Health score dischi

📦 **Duplicati** (Backup Versioning)
🔗 http://192.168.1.50:8200
• Backup automatico /mnt/nas/docker/
• Versioning multiplo (2 versioni)
• Scheduling personalizzabile

⏰ **Uptime Kuma** (Service Monitoring)
🔗 http://192.168.1.50:3001
• Monitoring VPN transmission-openvpn
• Alert downtime servizi
• Grafici uptime

🐳 **Portainer** (Container Management)
🔗 http://192.168.1.50:9000
• Gestione 26 container esistenti
• Logs e statistiche
• Stack management

🤖 **MANUTENZIONE AUTOMATICA:**
• BTRFS scrub: Domenica 2:00
• BTRFS balance: 1° mese 3:00
• BTRFS defrag: 15° mese 4:00

💬 **Telegram Bot AI**
• Chat intelligente con contesto NAS
• Analisi problemi e soluzioni
• Integration con tutti i servizi"""

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message,
        parse_mode='Markdown'
    )

async def cmd_help(update, context):
    """Help comandi"""
    if not await check_authorization(update, context):
        return
        
    help_text = """🤖 **NAS AI Assistant Enhanced**

⚡ **Comandi Veloci:**
/status - Dashboard completo NAS
/vpn - Check transmission-openvpn
/services - Lista servizi web
/help - Questo messaggio

🤖 **Analisi AI (1-2 min):**
/disks - Analisi SMART dischi
/containers - Status container Docker  
/backup - Stato sistema backup

💬 **Chat AI Libera:**
Scrivi qualsiasi domanda! Esempi:
• "Temperature anomale?"
• "Container problematici?"
• "Come sta il RAID1?"
• "Backup funziona?"
• "VPN transmission OK?"

✨ **Il bot ha accesso a:**
• Netdata (monitoring real-time)
• Scrutiny (SMART dischi)
• Docker API (26 container)
• Uptime Kuma (servizi)
• Duplicati (backup)
• Filesystem BTRFS /mnt/nas

🔧 **Web Interface:**
Usa /services per URLs completi dei servizi di monitoring web.

🎯 **AI Enhancement:**
Il bot analizza automaticamente:
• Performance sistema
• Stato container critici
• Temperature dischi
• Connessione VPN transmission
• Spazio storage BTRFS"""

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=help_text,
        parse_mode='Markdown'
    )

async def handle_message(update, context):
    """Gestisce chat AI con contesto completo"""
    if not await check_authorization(update, context):
        return
        
    user_message = update.message.text
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, 
        action=telegram.constants.ChatAction.TYPING
    )
    
    response = await ollama_query(user_message, include_context=True)
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=response
    )

if __name__ == "__main__":
    print("🤖 NAS Telegram Bot Enhanced - Starting...")
    print(f"📊 Netdata: {NETDATA_URL}")
    print(f"💾 Scrutiny: {SCRUTINY_URL}")  
    print(f"📦 Duplicati: {DUPLICATI_URL}")
    print(f"⏰ Uptime Kuma: {UPTIME_KUMA_URL}")
    print(f"🐳 Portainer: {PORTAINER_URL}")
    print(f"🤖 Ollama: {OLLAMA_URL}")
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers comandi
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("vpn", cmd_vpn))
    app.add_handler(CommandHandler("disks", cmd_disks))
    app.add_handler(CommandHandler("containers", cmd_containers))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("services", cmd_services))
    
    # Chat AI handler
    app.add_handler(MessageHandler(
        filters.TEXT & (~filters.COMMAND), 
        handle_message
    ))
    
    print("✅ Bot Enhanced attivo!")
    print("🔧 Servizi integrati: Netdata + Scrutiny + Duplicati + Uptime Kuma")
    print("💾 Backup source: /mnt/nas/docker/")
    print("🤖 AI con contesto completo NAS")
    app.run_polling()