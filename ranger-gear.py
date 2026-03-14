import cv2
import numpy as np
import subprocess
import os
import time
from time import sleep
import sys
import shutil
import glob
import tempfile
import json
import threading
import queue
import concurrent.futures
import argparse
import colorama
from colorama import Fore, Style
import ssl
from datetime import datetime
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

# Try to import customtkinter for the modern UI
try:
    import customtkinter as ctk
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    print("[WARN] customtkinter not found. GUI mode will be disabled. Run 'pip install customtkinter' to enable.")

colorama.init(autoreset=True)

# Fix SSL certificate error for downloading EasyOCR models
ssl._create_default_https_context = ssl._create_unverified_context

# =========================================================
# Statistics and GUI Tracking
# =========================================================
# ----- Simplified UI Stats Class -----
class SimpleUIStats:
    def __init__(self):
        self.total_files = 0
        self.successful_logins = 0
        self.failed_logins = 0
        self.processed_files = 0
        self.connected_devices = 0
        self.lock = threading.RLock()
        self.last_update = time.time()
        self.update_interval = 30
        self.device_statuses = {}
        self.hero_counts = {}
        # Counter สำหรับ hero found/not-found
        self.success_count = 0 # Matches bot success_count
        self.fail_count = 0    # Matches bot fail_count
        # hero found list with counts
        self.hero_found_list = {}  # {hero_combo: count} e.g. {'Yor': 1, 'Yor+Anya': 2}
        
    def _get_shared_file(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared_stats.json")

    def save_shared(self):
        """Save stats to a shared file for multi-process sync (Atomic write)"""
        try:
            with self.lock:
                data = {
                    "success_count": self.success_count,
                    "fail_count": self.fail_count,
                    "hero_found_list": self.hero_found_list,
                    "device_statuses": self.device_statuses,
                    "last_update": time.time()
                }
                path = self._get_shared_file()
                tmp_path = path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                # Atomic replace with retry for Windows WinError 32
                for _ in range(5):
                    try:
                        os.replace(tmp_path, path)
                        break
                    except OSError:
                        time.sleep(0.1)
                else:
                    # Fallback if replace keeps failing
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        except Exception as e:
            print(f"[DEBUG] save_shared error: {e}")

    def load_shared(self):
        """Load stats from the shared file with retries"""
        shared_file = self._get_shared_file()
        if not os.path.exists(shared_file):
            return
            
        for _ in range(5): # Retry up to 5 times
            try:
                with open(shared_file, "r", encoding="utf-8") as f:
                    content = f.read()
                    if not content: continue
                    data = json.loads(content)
                    with self.lock:
                        # Only update if shared data is newer or to merge
                        self.success_count = max(self.success_count, data.get("success_count", 0))
                        self.fail_count = max(self.fail_count, data.get("fail_count", 0))
                        
                        # Merge hero lists (take max count)
                        shared_heroes = data.get("hero_found_list", {})
                        for h, count in shared_heroes.items():
                            self.hero_found_list[h] = max(self.hero_found_list.get(h, 0), count)
                            
                        # Update device statuses
                        self.device_statuses.update(data.get("device_statuses", {}))
                break
            except Exception as e:
                time.sleep(0.1)

    def update(self, total=None, processed=None, success=None, fail=None, devices=None, hero_found=None, hero_not_found=None):
        self.load_shared() # Pull latest from others first to avoid overwriting counts
        with self.lock:
            if total is not None: self.total_files = total
            if processed is not None: self.processed_files = processed
            if success is not None: 
                # For success/fail, we take the max of (local incremented) vs (shared latest)
                # This is safer than just setting it.
                self.success_count = max(self.success_count, success)
            if fail is not None: 
                self.fail_count = max(self.fail_count, fail)
            if devices is not None: self.connected_devices = devices
            if hero_found is not None: self.success_count += hero_found
            if hero_not_found is not None: self.fail_count += hero_not_found
            self.save_shared()
    
    def update_device(self, device_serial, status):
        """Update device status and sync with shared file"""
        self.load_shared() # Pull latest from others first
        with self.lock:
            self.device_statuses[device_serial] = status
            self.save_shared() # Save merged state back
    
    def update_hero(self, hero_name, count=1):
        """Update hero found count and sync"""
        self.load_shared() # Pull latest first
        with self.lock:
            if hero_name not in self.hero_found_list:
                self.hero_found_list[hero_name] = 0
            self.hero_found_list[hero_name] += count
            self.save_shared()

    def get_hero_combo_stats(self):
        self.load_shared() # Always refresh before getting
        with self.lock:
            return dict(self.hero_found_list)

ui_stats = SimpleUIStats()
GUI_INSTANCE = None

if GUI_AVAILABLE:
    class CollabConfigWindow(ctk.CTkToplevel):
        def __init__(self, parent):
            super().__init__(parent)
            self.title("⚙ Config Settings")
            self.geometry("350x380")
            self.resizable(False, False)
            self.transient(parent)
            self.grab_set()
            
            ctk.CTkLabel(self, text="Application Settings", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10, 5))
            
            # Use switches for main modes
            self.vars = {}
            self.add_switch("Find Ranger", "find_ranger")
            self.add_switch("Find Gear", "find_gear")
            self.add_switch("Find Both (All)", "find_all")
            self.add_switch("First Loop Process", "first_loop")
            self.add_switch("Custom Mode", "custommode")
            
            # Thread delay entry
            delay_frame = ctk.CTkFrame(self, fg_color="transparent")
            delay_frame.pack(fill="x", padx=20, pady=5)
            ctk.CTkLabel(delay_frame, text="Thread Delay (sec):").pack(side="left")
            self.ent_delay = ctk.CTkEntry(delay_frame, width=60)
            self.ent_delay.insert(0, str(config.get("thread_delay", 5)))
            self.ent_delay.pack(side="right")
            
            ctk.CTkButton(self, text="💾 Save Changes", command=self.save_config, fg_color="#2cc985", hover_color="#229f69", height=32).pack(pady=20)
            
        def add_switch(self, label, key):
            var = tk.IntVar(value=config.get(key, 0))
            self.vars[key] = var
            chk = ctk.CTkSwitch(self, text=label, variable=var)
            chk.pack(pady=5, padx=25, anchor="w")

        def save_config(self):
            # Update global config and save to file
            for key, var in self.vars.items():
                config[key] = var.get()
            
            try:
                config["thread_delay"] = int(self.ent_delay.get())
            except:
                pass
                
            main_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ranger-gear_config.json")
            try:
                with open(main_config_file, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
                print(f"[CONFIG] Saved updated settings to {main_config_file}")
            except Exception as e:
                print(f"[ERR] Failed to save config: {e}")
            self.destroy()

    class HeroFoldersWindow(ctk.CTkToplevel):
        def __init__(self, parent):
            super().__init__(parent)
            self.title("🦸 Hero Folders")
            self.geometry("320x400")
            self.parent = parent
            self.resizable(False, False)
            self.transient(parent)
            self.grab_set()
            self.focus_force()
            
            ctk.CTkLabel(self, text="Hero Folders", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(8, 5))
            self.scroll_frame = ctk.CTkScrollableFrame(self, width=280, height=300)
            self.scroll_frame.pack(fill="both", expand=True, padx=10, pady=5)
            self.load_hero_folders()
            
        def load_hero_folders(self):
            # Show "no-find" and categories if they exist
            base_dir = os.path.join(os.getcwd(), "backup-id")
            if os.path.exists(base_dir):
                for folder in os.listdir(base_dir):
                    btn = ctk.CTkButton(self.scroll_frame, text=f"📁 {folder}", fg_color="#2a3a5c", height=28, anchor="w",
                                        command=lambda f=folder: subprocess.Popen(f'explorer "{os.path.join(base_dir, f)}"'))
                    btn.pack(fill="x", pady=1)

    class DeviceMonitorWidget(ctk.CTkFrame):
        def __init__(self, parent, device_id, index):
            super().__init__(parent, fg_color="#383838", corner_radius=6, height=32)
            self.device_id = device_id
            self.pack_propagate(False)
            
            chk = ctk.CTkCheckBox(self, text="", width=20, height=20, checkbox_width=16, checkbox_height=16)
            chk.pack(side="left", padx=(6, 2))
            chk.select()
            
            ctk.CTkLabel(self, text=f"#{index}", font=ctk.CTkFont(size=11, weight="bold"), text_color="#ffffff", width=25).pack(side="left", padx=(0, 4))
            ctk.CTkLabel(self, text=device_id, font=ctk.CTkFont(family="Consolas", size=10), text_color="#ccc").pack(side="left", padx=(0, 6))
            
            self.lbl_status = ctk.CTkLabel(self, text="Ready", font=ctk.CTkFont(size=10, weight="bold"), text_color="#4caf50", width=60)
            self.lbl_status.pack(side="right", padx=6)
            
            ctk.CTkButton(self, text="↺", width=22, height=20, font=ctk.CTkFont(size=11, weight="bold"), fg_color="#e53935").pack(side="right", padx=2)

        def update_state(self, status=None, **kwargs):
            if status:
                color_map = {'working': "#4caf50", 'waiting': "#ff9800", 'error': "#e53935", 'idle': "#888"}
                self.lbl_status.configure(text=status.upper(), text_color=color_map.get(status, "#888"))

    class ConsoleRedirector:
        """Redirect print() output to both console AND GUI log area"""
        def __init__(self, original_stdout, log_queue):
            self.original_stdout = original_stdout
            self.log_queue = log_queue
        
        def write(self, message):
            if self.original_stdout:
                self.original_stdout.write(message)
            # Only queue non-empty, non-newline messages
            if message and message.strip():
                try:
                    self.log_queue.put_nowait(message.strip())
                except:
                    pass
        
        def flush(self):
            if self.original_stdout:
                self.original_stdout.flush()

    class ModernBotGUI(ctk.CTk):
        def __init__(self, devices, args):
            super().__init__()
            global GUI_INSTANCE
            GUI_INSTANCE = self
            
            self.title("Ranger+Gear")
            self.geometry("620x530")
            self.devices = devices
            self.args = args
            self.bot_threads = []
            self.device_monitors = {}
            self.hero_stats_labels = {}
            self.hero_rows = {}
            self.hero_filter_text = ""
            self.is_started = False
            
            self.setup_ui()
            
            # Setup console redirect to GUI log
            self._log_queue = queue.Queue()
            sys.stdout = ConsoleRedirector(sys.__stdout__, self._log_queue)
            
            # Handle window close
            self.protocol("WM_DELETE_WINDOW", self.on_closing)
            
            # Use after to start the stats loop without blocking the constructor
            self.after(100, self.update_realtime_stats)
            self.after(100, self.process_log_queue)
            
            # Ensure window is visible
            self.deiconify()
            self.focus_force()
            print("[GUI] Launched Successfully. Waiting for manual start.")
            
            if getattr(self.args, 'no_start', False):
                print("[GUI] Monitor mode active (No internal threads).")
                self.lbl_auto_start.configure(text="[ DASHBOARD MODE ]", text_color="#ffae42")
            else:
                self.lbl_auto_start.configure(text="[ WAITING FOR START ]", text_color="#aaaaaa")

        def setup_ui(self):
            # 1. TOP TOOLBAR
            toolbar = ctk.CTkFrame(self, height=40, fg_color="#333333", corner_radius=0)
            toolbar.pack(fill="x")
            toolbar.pack_propagate(False)
            
            self.lbl_status = ctk.CTkLabel(toolbar, text=f"   ● ONLINE ({len(self.devices)})", font=ctk.CTkFont(size=12, weight="bold"), text_color="#4caf50")
            self.lbl_status.pack(side="left", padx=5)

            self.btn_start = ctk.CTkButton(toolbar, text="▶ START", font=ctk.CTkFont(size=12, weight="bold"), width=80, height=24, fg_color="#e53935", hover_color="#c62828", command=self.start_bot)
            self.btn_start.pack(side="left", padx=10)
            
            self.lbl_auto_start = ctk.CTkLabel(toolbar, text="[ WAITING FOR START ]", font=ctk.CTkFont(size=10, weight="bold"), text_color="#aaaaaa")
            self.lbl_auto_start.pack(side="left", padx=5)
            
            # Stats on Toolbar (right)
            counter_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
            counter_frame.pack(side="right", padx=8)
            
            self.lbl_succ_count = ctk.CTkLabel(counter_frame, text="✅ 0", font=ctk.CTkFont(size=12, weight="bold"), text_color="#4caf50")
            self.lbl_succ_count.pack(side="right", padx=6)
            
            self.lbl_fail_count = ctk.CTkLabel(counter_frame, text="❌ 0", font=ctk.CTkFont(size=12, weight="bold"), text_color="#ff5555")
            self.lbl_fail_count.pack(side="right", padx=6)
            
            self.lbl_file_count = ctk.CTkLabel(counter_frame, text="📁 0", font=ctk.CTkFont(size=12, weight="bold"), text_color="#aaaaaa")
            self.lbl_file_count.pack(side="right", padx=6)
            
            # 2. MAIN CONTENT
            main_frame = ctk.CTkFrame(self, fg_color="transparent")
            main_frame.pack(fill="both", expand=True, padx=6, pady=4)
            main_frame.grid_columnconfigure(0, weight=3)
            main_frame.grid_columnconfigure(1, weight=2)
            main_frame.grid_rowconfigure(0, weight=1)
            
            # Left: Devices
            left_frame = ctk.CTkFrame(main_frame, fg_color="#2b2b2b", corner_radius=8)
            left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
            
            dev_header = ctk.CTkFrame(left_frame, fg_color="#383838", corner_radius=0, height=28)
            dev_header.pack(fill="x")
            ctk.CTkLabel(dev_header, text="   DEVICES", font=ctk.CTkFont(size=11, weight="bold"), text_color="#cccccc", anchor="w").pack(side="left")
            
            self.dev_scroll = ctk.CTkScrollableFrame(left_frame, fg_color="transparent")
            self.dev_scroll.pack(fill="both", expand=True, padx=3, pady=3)
            for i, dev in enumerate(self.devices):
                m = DeviceMonitorWidget(self.dev_scroll, dev, i+1)
                m.pack(fill="x", pady=1)
                self.device_monitors[dev] = m
            
            # Right: Heroes
            right_frame = ctk.CTkFrame(main_frame, fg_color="#2b2b2b", corner_radius=8)
            right_frame.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
            
            hero_header = ctk.CTkFrame(right_frame, fg_color="#383838", corner_radius=0, height=56)
            hero_header.pack(fill="x")
            hero_header.pack_propagate(False)
            
            title_row = ctk.CTkFrame(hero_header, fg_color="transparent", height=28)
            title_row.pack(fill="x")
            ctk.CTkLabel(title_row, text="   🏆 HEROES FOUND", font=ctk.CTkFont(size=11, weight="bold"), text_color="#f2c94c", anchor="w").pack(side="left")
            self.lbl_filter_count = ctk.CTkLabel(title_row, text="Filtered: 0", font=ctk.CTkFont(size=10), text_color="#aaaaaa")
            self.lbl_filter_count.pack(side="right", padx=10)
            
            # Filter Entry
            filter_frame = ctk.CTkFrame(hero_header, fg_color="transparent", height=24)
            filter_frame.pack(fill="x", padx=5, pady=2)
            self.ent_filter = ctk.CTkEntry(filter_frame, placeholder_text="🔍 Search heroes or gear (e.g. lapel)...", font=ctk.CTkFont(size=11), height=22, fg_color="#1e1e1e", border_width=1)
            self.ent_filter.pack(fill="x", expand=True)
            self.ent_filter.bind("<KeyRelease>", lambda e: self.on_filter_changed())
            
            self.hero_scroll = ctk.CTkScrollableFrame(right_frame, fg_color="transparent")
            self.hero_scroll.pack(fill="both", expand=True, padx=3, pady=3)
            
            # 3. LOG AREA
            log_frame = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=6, height=80)
            log_frame.pack(fill="x", padx=6, pady=(0, 4))
            log_frame.pack_propagate(False)
            
            self.log_text = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=10), text_color="#8b949e", fg_color="#1e1e1e")
            self.log_text.pack(fill="both", expand=True, padx=2, pady=2)
            self.log_text.configure(state="disabled")
            
            # 4. BOTTOM BAR
            bottom_bar = ctk.CTkFrame(self, height=32, fg_color="#333333", corner_radius=0)
            bottom_bar.pack(fill="x")
            
            base_path = os.path.dirname(os.path.abspath(__file__))
            backup_folder = os.path.join(base_path, "backup")
            heroes_folder = os.path.join(base_path, "backup-id")
            
            ctk.CTkButton(bottom_bar, text="🔌 Connect Missing", width=85, height=22, font=ctk.CTkFont(size=10), fg_color="#4caf50", command=self.connect_missing_devices).pack(side="left", padx=3, pady=4)
            ctk.CTkButton(bottom_bar, text="⚙ Config", width=70, height=22, font=ctk.CTkFont(size=10), fg_color="#555555", command=self.open_config).pack(side="left", padx=3, pady=4)
            ctk.CTkButton(bottom_bar, text="📁 Backup", width=70, height=22, font=ctk.CTkFont(size=10), fg_color="#555555", command=lambda: subprocess.Popen(f'explorer "{backup_folder}"')).pack(side="left", padx=3, pady=4)
            ctk.CTkButton(bottom_bar, text="🦸 Heroes", width=70, height=22, font=ctk.CTkFont(size=10), fg_color="#555555", command=lambda: subprocess.Popen(f'explorer "{heroes_folder}"')).pack(side="left", padx=3, pady=4)
            ctk.CTkLabel(bottom_bar, text="v3.2.0", font=ctk.CTkFont(size=10), text_color="#888888").pack(side="right", padx=8)

        def connect_missing_devices(self):
            """Scan for missing adb connections and start them dynamically"""
            self.log("INFO", "Scanning for missing emulators...")
            # Automatically perform port scan before checking devices
            connect_known_ports()
            
            current_devices = get_connected_devices()
            emulator_devices = [d for d in current_devices if d.startswith("emulator-") or d.startswith("127.0.0.1:")]
            
            new_count = 0
            for dev in emulator_devices:
                if dev not in self.devices:
                    new_count += 1
                    self.devices.append(dev)
                    # Add to UI
                    m = DeviceMonitorWidget(self.dev_scroll, dev, len(self.devices))
                    m.pack(fill="x", pady=1)
                    self.device_monitors[dev] = m
                    
                    # Start bot thread
                    if getattr(self, 'is_started', False) and not getattr(self.args, 'no_start', False):
                        bot = RangerGearBot(dev, self.args)
                        bot.start()
                        self.bot_threads.append(bot)
                    self.log("SUCCESS", f"Connected new device: {dev}")
            
            if new_count > 0:
                self.lbl_status.configure(text=f"   ● ONLINE ({len(self.devices)})")
            else:
                self.log("INFO", "No new devices found.")

        def log(self, level, message): 
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{ts}] {message}\n")
            # Keep log area from growing too large (max 500 lines)
            line_count = int(self.log_text.index('end-1c').split('.')[0])
            if line_count > 500:
                self.log_text.delete('1.0', f'{line_count - 400}.0')
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def process_log_queue(self):
            """Process pending log messages from bot threads"""
            try:
                max_per_tick = 20  # Process up to 20 messages per tick to avoid freezing
                count = 0
                while not self._log_queue.empty() and count < max_per_tick:
                    msg = self._log_queue.get_nowait()
                    self.log("BOT", msg)
                    count += 1
            except:
                pass
            self.after(200, self.process_log_queue)

        def _start_single_bot(self, device_id):
            bot = RangerGearBot(device_id, self.args)
            bot.start()
            self.bot_threads.append(bot)
            self.log("INFO", f"🚀 Started bot on {device_id}")

        def start_bot(self):
            if getattr(self, 'is_started', False):
                self.log("WARN", "Bot is already running.")
                return
            self.is_started = True
            if hasattr(self, 'btn_start'):
                self.btn_start.configure(state="disabled", fg_color="#555555", text="⏳ RUNNING")
            self.lbl_auto_start.configure(text="[ BOT IS RUNNING ]", text_color="#4caf50")
            
            delay_sec = config.get("thread_delay", 5)
            self.log("INFO", f"Starting Bot Threads (Delay: {delay_sec}s per device)...")
            
            for i, device_id in enumerate(self.devices):
                delay_ms = i * int(delay_sec) * 1000
                # Pass device_id explicitly by freezing the variable in the lambda
                self.after(delay_ms, lambda d=device_id: self._start_single_bot(d))

        def on_closing(self):
            if messagebox.askokcancel("Quit", "คุณต้องการหยุดบอทและปิดโปรแกรมใช่หรือไม่?\n(จะทำการ Kill ADB และ Python ทั้งหมด)"):
                print("[GUI] Shutting down... Killing background processes.")
                try:
                    # Kill ADB and Python processes on Windows
                    if os.name == 'nt':
                        subprocess.run("taskkill /F /IM adb.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run("taskkill /F /IM python.exe /T", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except:
                    pass
                sys.exit(0)

        def update_realtime_stats(self):
            try:
                # Load shared stats from other processes
                ui_stats.load_shared()
                
                with ui_stats.lock:
                    # Count files real-time in the backup folder
                    source_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup")
                    qsize = 0
                    if os.path.exists(source_folder):
                        qsize = len([f for f in os.listdir(source_folder) if f.lower().endswith(".xml")])
                    
                    self.lbl_file_count.configure(text=f"📁 {qsize}")
                    self.lbl_succ_count.configure(text=f"✅ {ui_stats.success_count}")
                    self.lbl_fail_count.configure(text=f"❌ {ui_stats.fail_count}")
                    
                    for dev, stat in ui_stats.device_statuses.items():
                        if dev in self.device_monitors:
                            self.device_monitors[dev].update_state(status=stat.get('status'))
                    
                    hero_raw_data = ui_stats.get_hero_combo_stats()
                    hero_data = hero_raw_data.copy()
                    
                    # 1. Handle Login Failures separately from Scan Failures
                    login_fail_count = ui_stats.fail_count
                    if login_fail_count > 0:
                        hero_data["❌ เข้าไม่ได้ (Login Failed)"] = login_fail_count
                    
                    # 2. Handle "Success but No Hero/Gear Found"
                    not_found_success = hero_data.pop("ไม่เจอ", 0)
                    if not_found_success > 0:
                        hero_data["🔍 สแกนไม่เจอ (Not Found)"] = not_found_success
                    
                    for hero, count in hero_data.items():
                        if hero not in self.hero_stats_labels:
                            # Color coding: Red for failures/not found, Green for success
                            is_error_row = any(x in hero for x in ["เข้าไม่ได้", "สแกนไม่เจอ"])
                            self.add_hero_row(hero, is_error_row)
                        
                        self.hero_stats_labels[hero].configure(text=str(count))
                    
                    # Explicitly hide old "ไม่เจอ" or "❌ ไม่เจอ" rows if they exist from previous versions
                    for old_key in ["ไม่เจอ", "❌ ไม่เจอ"]:
                        if old_key in self.hero_rows:
                            self.hero_rows[old_key].pack_forget()
                    
                    # Update Filter
                    self.filter_heroes()
            except Exception as e:
                print(f"[GUI] Update error: {e}")
            
            self.after(500, self.update_realtime_stats)

        def on_filter_changed(self):
            self.hero_filter_text = self.ent_filter.get().lower()
            self.filter_heroes()

        def filter_heroes(self):
            total_filtered = 0
            for hero, row in self.hero_rows.items():
                if not self.hero_filter_text or self.hero_filter_text in hero.lower():
                    row.pack(fill="x", pady=1)
                    # Get count from label text
                    try:
                        count = int(self.hero_stats_labels[hero].cget("text"))
                        total_filtered += count
                    except: pass
                else:
                    row.pack_forget()
            
            if hasattr(self, 'lbl_filter_count'):
                self.lbl_filter_count.configure(text=f"Filtered: {total_filtered}")


        def add_hero_row(self, hero_name, is_not_found):
            bg = "#3d2020" if is_not_found else "#2a3a2a"
            txt_color = "#e53935" if is_not_found else "#4caf50"
            row = ctk.CTkFrame(self.hero_scroll, fg_color=bg, corner_radius=6, height=26)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)
            ctk.CTkLabel(row, text=f"  {hero_name}", font=ctk.CTkFont(size=11, weight="bold"), text_color="white", anchor="w").pack(side="left", fill="x", expand=True)
            lbl_count = ctk.CTkLabel(row, text="0", font=ctk.CTkFont(size=12, weight="bold"), text_color=txt_color)
            lbl_count.pack(side="right", padx=8)
            self.hero_stats_labels[hero_name] = lbl_count
            self.hero_rows[hero_name] = row

        def open_config(self): CollabConfigWindow(self)
        def open_heroes(self): HeroFoldersWindow(self)

# =============================================================
# Global Config
# =============================================================
# Default config (will be overridden by config files)
config = {
    "first_loop": True,
    "thread_delay": 5,
    "find_ranger": 0,
    "find_gear": 0,
    "find_all": 1,
    "custommode": 0,
    "custom": {"characters": []},
    "characters": [],
    "ranger_images": {},
    "gearname": {},
    "weaponname": {},
    "ocr_region": {"x": 463, "y": 153, "w": 397, "h": 321}
}

adb_path = "adb"

# EasyOCR reader - loaded once globally
_ocr_reader = None
_ocr_lock = threading.Lock()  # Thread-safe OCR init

def get_ocr_reader():
    """Get or create EasyOCR reader (singleton, thread-safe)"""
    global _ocr_reader
    if _ocr_reader is None:
        with _ocr_lock:
            if _ocr_reader is None:
                import easyocr
                print("[INFO] Loading EasyOCR model (first time only)...")
                _ocr_reader = easyocr.Reader(['en'], gpu=False)
                print("[OK] EasyOCR model loaded!")
    return _ocr_reader


def load_config():
    global config
    
    # Load ONLY main config from ranger-gear_config.json
    main_config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ranger-gear_config.json")
    if os.path.exists(main_config_file):
        try:
            with open(main_config_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                config.update(loaded)
            print(f"[CONFIG] Base Loaded: {main_config_file}")
        except Exception as e:
            print(f"[WARN] Error loading config: {e}")
    else:
        print(f"[WARN] Config not found: {main_config_file}")


def find_adb_executable():
    global adb_path
    
    # Check common locations
    script_dir = os.path.dirname(os.path.abspath(__file__))
    adb_locations = [
        os.path.join(script_dir, "adb", "adb.exe"),
        os.path.join(script_dir, "adb", "adb"),
        "adb",
    ]
    
    # Add current working directory as another check
    adb_locations.append(os.path.join(os.getcwd(), "adb", "adb.exe"))
    
    for loc in adb_locations:
        if not loc.endswith(".exe") and sys.platform == 'win32' and not os.path.isabs(loc):
             pass # Skip simple "adb" for exists check if it's just a command
        elif os.path.exists(loc):
            print(f"[ADB] Found file at {loc}, testing...")
            try:
                result = subprocess.run(
                    [loc, "version"],
                    capture_output=True, text=True, timeout=5,
                    shell=(sys.platform == 'win32')
                )
                if result.returncode == 0:
                    adb_path = loc
                    print(f"[ADB] Verified: {adb_path}")
                    return True
            except Exception as e:
                print(f"[ADB] Error testing {loc}: {e}")
        
        # Also try running loc directly if it's a command name like "adb"
        if loc == "adb":
            try:
                result = subprocess.run(
                    [loc, "version"],
                    capture_output=True, text=True, timeout=5,
                    shell=(sys.platform == 'win32')
                )
                if result.returncode == 0:
                    adb_path = loc
                    print(f"[ADB] Verified command: {adb_path}")
                    return True
            except:
                pass
    
    # Try system PATH
    adb_in_path = shutil.which("adb")
    if adb_in_path:
        adb_path = os.path.abspath(adb_in_path)
        print(f"[ADB] Found in PATH: {adb_path}")
        return True
    
    # Try common fallback "adb" string
    try:
        subprocess.run(["adb", "--version"], capture_output=True, timeout=5, check=True)
        adb_path = "adb"
        print(f"[ADB] Found 'adb' command in system")
        return True
    except:
        pass
    
    # Try MuMu emulator paths
    mumu_adb_paths = [
        "F:\\Program Files\\Netease\\MuMuPlayer\\shell\\adb.exe",
        "C:\\Program Files\\Netease\\MuMuPlayerGlobal-12.0\\shell\\adb.exe",
        "C:\\Program Files\\Netease\\MuMuPlayer\\shell\\adb.exe",
        "F:\\MuMuPlayerGlobal-12.0\\shell\\adb.exe",
        "D:\\Program Files\\Netease\\MuMuPlayer\\shell\\adb.exe",
        "E:\\Program Files\\Netease\\MuMuPlayer\\shell\\adb.exe"
    ]
    
    for path in mumu_adb_paths:
        if os.path.exists(path):
            adb_path = path
            print(f"[ADB] Found MuMu ADB: {path}")
            return True
    
    return False


def connect_known_ports():
    """Auto-scan ALL emulator ports, connect everything that responds"""
    try:
        # Kill & start adb server
        subprocess.run([adb_path, "kill-server"], capture_output=True, timeout=3)
        time.sleep(0.1)
        subprocess.run([adb_path, "start-server"], capture_output=True, timeout=3)
        time.sleep(0.5)

        # สแกนพอร์ตคี่ตั้งแต่ 5555-5755 (รองรับ 100 จอ MuMu)
        ports = list(range(5555, 5756, 2))  # [5555, 5557, 5559, ..., 5755]

        print(f"\n--- [ADB] Auto-scanning {len(ports)} ports (5555-5755 odd) ---")
        
        connected = []
        
        def try_connect_port(port):
            """ยิงเชื่อมต่อทีละพอร์ต"""
            try:
                addr = f"127.0.0.1:{port}"
                result = subprocess.run(
                    [adb_path, "connect", addr],
                    capture_output=True, timeout=1, text=True
                )
                out = result.stdout.lower()
                if ("connected" in out or "already connected" in out) and "cannot" not in out:
                    return addr
            except Exception:
                pass
            return None

        # ยิงเชื่อมต่อพร้อมกัน
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(try_connect_port, p): p for p in ports}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    connected.append(result)
        
        if connected:
            print(f"[ADB] Port scan found {len(connected)} device(s): {', '.join(sorted(connected))}")
        else:
            print("[ADB] Port scan found no devices.")
                
        print("--- Scan Complete ---\n")
    except Exception as e:
        print(f"[ADB] Port scan error: {e}")


def get_connected_devices():
    """ดึงรายชื่อ devices ที่ online จาก adb devices (ไม่จำกัดจำนวน, กรองซ้ำ)"""
    try:
        result = subprocess.run(
            [adb_path, "devices"],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().split("\n")[1:]
        raw_devices = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                raw_devices.append(parts[0])
        
        if not raw_devices:
            return []
                
        # กรองซ้ำ: ถ้ามี emulator-5556 อยู่แล้ว ไม่ต้องเอา 127.0.0.1:5557 อีก
        emulator_adb_ports = set()  # เก็บพอร์ต ADB (คี่) ที่ emulator-xxx ครอง
        for d in raw_devices:
            if d.startswith("emulator-"):
                try:
                    console_port = int(d.replace("emulator-", ""))
                    emulator_adb_ports.add(console_port + 1)  # emulator-5556 -> ADB port 5557
                except ValueError:
                    pass
        
        final_devices = []
        seen = set()
        for d in raw_devices:
            if d in seen:
                continue
            # ถ้าเป็น 127.0.0.1:port แล้วมี emulator- ครองอยู่แล้ว -> ข้าม
            if d.startswith("127.0.0.1:"):
                try:
                    port = int(d.split(":")[1])
                    if port in emulator_adb_ports:
                        continue  # ซ้ำกับ emulator-xxxx
                except ValueError:
                    pass
            seen.add(d)
            final_devices.append(d)
        
        return final_devices
    except Exception as e:
        print(f"[ERR] get_connected_devices: {e}")
        return []


# =============================================================
# RangerGearBot Class - Unified Bot for Ranger + Gear
# =============================================================
class RangerGearBot(threading.Thread):
    def __init__(self, device_id, args=None):
        threading.Thread.__init__(self)
        self.device_id = device_id
        self.args = args # Store command line args
        self.daemon = True
        
        def update_gui_status(self, step, status="working"):
            ui_stats.update_device(self.device_id, {'step': step, 'status': status})
        self.update_gui_status = update_gui_status.__get__(self, RangerGearBot)
        
        # Determine which modes to run
        self.do_ranger = config.get("find_ranger", 0) or config.get("find_all", 1)
        self.do_gear = config.get("find_gear", 0) or config.get("find_all", 1)
        
        print(f"[{self.device_id}] Mode - Ranger: {self.do_ranger}, Gear: {self.do_gear}")
        
        # Unique filename for this thread
        safe_dev = device_id.replace(":", "_")
        self.filename = os.path.join(tempfile.gettempdir(), f"screen-{safe_dev}.png")
        self.first_loop_done = not config.get("first_loop", True)
        
        # Ranger Config
        if self.do_ranger:
            if config.get("custommode") == 1:
                custom_data = config.get("custom", {})
                self.characters = custom_data.get("characters", [])
                print(f"[{self.device_id}] Custom mode (custommode=1) -> searching: {self.characters}")
            else:
                self.characters = config.get("characters", [])
                print(f"[{self.device_id}] Find-all ranger mode -> searching {len(self.characters)} characters")
            
            # Auto-scan img/ranger/ folder for all png files
            self.ranger_image_mapping = config.get("ranger_images", {})
            ranger_folder = os.path.join("img", "ranger")
            self.ranger_files = []
            if os.path.exists(ranger_folder):
                for f in sorted(os.listdir(ranger_folder)):
                    if f.lower().endswith(".png"):
                        self.ranger_files.append(f"ranger/{f}")
                print(f"[{self.device_id}] Auto-loaded {len(self.ranger_files)} ranger images from img/ranger/")
        
        # Gear Config
        if self.do_gear:
            self.gear_names = config.get("gearname", {})
            self.weapon_names = config.get("weaponname", {})
            self.ocr_region = config.get("ocr_region", {"x": 463, "y": 153, "w": 397, "h": 321})
            print(f"[{self.device_id}] Gear mode -> {len(self.gear_names)} gears to check")
        
        # Store original filename for backup
        self.current_original_filename = None
        
        # Sequence Definitions (Reverted to use coordinates for checkboxes)
        self.seq1 = ['icon.png', 'apple.png', '@check-l1.png', (932, 133), (930, 253), (926, 327), 'check-l4.png']
        self.seq2 = ['check-gusetid.png', 'check-gusetid1.png', '@check-l1.png', (932, 133), (930, 253), (926, 327), 'check-l4.png', 'check-ok1.png', 'check-ok2.png', 'check-ok3.png', 'check-ok4.png']
        
        self.adb_cmd = adb_path
        self._screen = None
        self._screen_color = None
        self._template_cache = {}

    def open_app(self):
        """เปิดแอป LINE Rangers ด้วยคำสั่ง am start / monkey (เร็วกว่าคลิก icon.png)"""
        attempt = 0
        while attempt < 5:
            attempt += 1
            try:
                if attempt % 2 == 1:
                    self.adb_run([
                        self.adb_cmd, "-s", self.device_id, "shell",
                        "am", "start", "-S", "-n",
                        "com.linecorp.LGRGS/com.linecorp.common.activity.LineActivity"
                    ], timeout=10)
                else:
                    self.adb_run([
                        self.adb_cmd, "-s", self.device_id, "shell",
                        "monkey", "-p", "com.linecorp.LGRGS",
                        "-c", "android.intent.category.LAUNCHER", "1"
                    ], timeout=10)
                
                sleep(3)
                
                try:
                    pid_result = subprocess.run(
                        [self.adb_cmd, "-s", self.device_id, "shell", "pidof", "com.linecorp.LGRGS"],
                        capture_output=True, text=True, timeout=5
                    )
                    pid = pid_result.stdout.strip()
                except Exception:
                    pid = ""
                
                if pid:
                    print(f"[{self.device_id}] ✓ App running (PID: {pid}) - attempt {attempt}")
                    return True
                else:
                    print(f"[{self.device_id}] ✗ App crashed/bounced! (attempt {attempt}) Retrying...")
                    sleep(2)
                    
            except Exception as e:
                print(f"[{self.device_id}] Error opening app (attempt {attempt}): {e}")
                sleep(2)
        
        print(f"[{self.device_id}] Failed to open app after 5 attempts!")
        return False

    def run(self):
        try:
            print(f"[{self.device_id}] RangerGear Bot Thread Started", flush=True)
            
            while True:
                # 0. Reload Config
                load_config()
                self.do_ranger = config.get("find_ranger", 0) or config.get("find_all", 1)
                self.do_gear = config.get("find_gear", 0) or config.get("find_all", 1)

                # 1. Look for next available file (Atomic Locking)
                xml_file = self._get_next_available_file()
                
                if not xml_file:
                    self.update_gui_status("Waiting for files", "waiting")
                    sleep(5)
                    continue

                try:
                    # Store original filename
                    self.current_original_filename = os.path.basename(xml_file)
                    
                    # 1. Check First Loop Process Toggle
                    current_first_loop_enabled = config.get("first_loop", True)
                    if current_first_loop_enabled and not self.first_loop_done:
                        self.update_gui_status("First Loop", "working")
                        res = self.first_loop_process()
                        if res == "complete":
                            self.first_loop_done = True
                        elif res == "restart":
                            # Cleanup lock if we need to restart the whole login
                            self._release_file_lock(xml_file)
                            sleep(2)
                            continue
                        elif res == "failed":
                            # Apple refresh limit reached -> move to login-failed and skip to next ID
                            print(f"[{self.device_id}] First loop FAILED (apple limit). Moving to login-failed and next ID...")
                            self.handle_failure(xml_file)
                            ui_stats.update(fail=ui_stats.fail_count + 1)
                            self.update_gui_status("Apple Failed", "error")
                            self._release_file_lock(xml_file)
                            self.first_loop_done = False
                            sleep(2)
                            continue
                    else:
                        self.first_loop_done = True
                    
                    print(f"[{self.device_id}] Processing file: {self.current_original_filename}")
                    self.update_gui_status(f"Injecting: {self.current_original_filename}")

                    # 2. Inject
                    injected_file = self.inject_file(xml_file)
                    
                    if injected_file:
                        # 3. Login
                        self.update_gui_status("Logging in...")
                        status = self.main_login(injected_file)
                        
                        if status == "success":
                            self.handle_success(xml_file)
                            ui_stats.update(success=ui_stats.success_count + 1, processed=ui_stats.processed_files + 1)
                            self.update_gui_status("Completed", "idle")
                        elif status == "kaiby":
                            self.handle_kaiby(xml_file)
                            ui_stats.update(fail=ui_stats.fail_count + 1)
                            self.update_gui_status("Kaiby Detected", "error")
                            self.first_loop_done = False
                        elif status == "failed":
                            self.handle_failure(xml_file)
                            ui_stats.update(fail=ui_stats.fail_count + 1)
                            self.update_gui_status("Failed", "error")
                            self.first_loop_done = False
                        else:
                            print(f"[{self.device_id}] Status: {status}. Moving to next.")
                            self.handle_failure(xml_file)
                            ui_stats.update(fail=ui_stats.fail_count + 1)
                            self.update_gui_status(f"Error: {status}", "error")
                    else:
                        print(f"[{self.device_id}] Injection failed for {xml_file}")
                        self.handle_dead_file(xml_file) # Move to failed if we can't even inject
                        ui_stats.update(fail=ui_stats.fail_count + 1)
                        self.update_gui_status("Inject Failed", "error")
                    
                    # Always ensure lock is removed after processing (handle_success/failure moves the file)
                    self._release_file_lock(xml_file)
                    
                except Exception as e:
                    print(f"[{self.device_id}] Critical Error with {xml_file}: {e}")
                    self._release_file_lock(xml_file)
                    sleep(5)
        except Exception as e:
            print(f"[{self.device_id}] Thread Crash: {e}", flush=True)

    def _get_lock_path(self, xml_file):
        """Get lock file path in temp directory (ไม่รก backup folder)"""
        lock_dir = os.path.join(tempfile.gettempdir(), "ranger-locks")
        if not os.path.exists(lock_dir):
            os.makedirs(lock_dir, exist_ok=True)
        lock_name = os.path.basename(xml_file) + ".lock"
        return os.path.join(lock_dir, lock_name)

    def _get_next_available_file(self):
        """Finds next .xml file in backup/ and attempts to lock it atomically."""
        source_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup")
        if not os.path.exists(source_folder): return None
        
        files = [os.path.join(source_folder, f) for f in os.listdir(source_folder) if f.lower().endswith(".xml")]
        # Shuffle files so multiple processes don't hit the exact same order
        import random
        random.shuffle(files)
        
        for xml_file in files:
            lock_file = self._get_lock_path(xml_file)
            
            # 1. Clean stale locks (> 30 mins)
            if os.path.exists(lock_file):
                if time.time() - os.path.getmtime(lock_file) > 1800:
                    try: os.remove(lock_file)
                    except: pass
                else: continue
            
            # 2. Try Atomic Lock (O_CREAT | O_EXCL)
            try:
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w') as f:
                    f.write(self.device_id)
                return xml_file
            except FileExistsError:
                continue
            except Exception as e:
                print(f"[LOCK] Error creating lock for {xml_file}: {e}")
                continue
                
        return None

    def _release_file_lock(self, xml_file):
        lock_file = self._get_lock_path(xml_file)
        if os.path.exists(lock_file):
            try: os.remove(lock_file)
            except: pass

    def handle_dead_file(self, file_path):
        """Move file that failed injection or has other issues"""
        dst_dir = "login-failed"
        if not os.path.exists(dst_dir): os.makedirs(dst_dir)
        base = os.path.basename(file_path)
        try: shutil.move(file_path, os.path.join(dst_dir, base))
        except: pass

    # =========================================================
    # File Handling
    # =========================================================
    def handle_success(self, file_path):
        dst_dir = "login-success"
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        base = os.path.basename(file_path)
        dst = os.path.join(dst_dir, base)
        try:
            shutil.move(file_path, dst)
            print(f"[{self.device_id}] Moved to {dst_dir}: {base}")
        except Exception as e:
            print(f"[{self.device_id}] Move error: {e}")

    def handle_failure(self, file_path):
        dst_dir = "login-failed"
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        base = os.path.basename(file_path)
        dst = os.path.join(dst_dir, base)
        
        print(f"[{self.device_id}] Login FAILED. Pulling file from device for debug...")
        
        # Pull the current file from the device to see its state
        src_remote = "/data/data/com.linecorp.LGRGS/shared_prefs/_LINE_COCOS_PREF_KEY.xml"
        temp_remote = f"/data/local/tmp/failed_pref_{self.device_id.replace(':','_')}.xml"
        
        try:
            self.adb_shell(f"su -c 'cp {src_remote} {temp_remote}'")
            self.adb_shell(f"su -c 'chmod 666 {temp_remote}'")
            self.adb_run([self.adb_cmd, "-s", self.device_id, "pull", temp_remote, dst])
            print(f"[{self.device_id}] Saved failed session file to {dst}")
        except Exception as e:
            print(f"[{self.device_id}] Failed to pull remote file: {e}")
            # Fallback: move the original local file
            try:
                if os.path.exists(file_path):
                    shutil.move(file_path, dst)
            except: pass

        # Clean up local backup file if it still exists
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except: pass

    def handle_kaiby(self, file_path):
        """Handle kaiby error by moving file to kaiby/ folder and clearing app"""
        dst_dir = "kaiby"
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        base = os.path.basename(file_path)
        dst = os.path.join(dst_dir, base)
        
        print(f"[{self.device_id}] KAIBY detected. Moving file to {dst_dir}/")
        
        # Clear app immediately
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.linecorp.LGRGS"])
        sleep(1)
        
        try:
            if os.path.exists(file_path):
                shutil.move(file_path, dst)
                print(f"[{self.device_id}] ✓ Moved to {dst_dir}: {base}")
        except Exception as e:
            print(f"[{self.device_id}] Kaiby move error: {e}")

    # =========================================================
    # Screen & Image Methods  
    # =========================================================
    @classmethod
    def _get_template(cls, template_path):
        if not hasattr(cls, '_template_cache_cls'):
            cls._template_cache_cls = {}
        
        if template_path not in cls._template_cache_cls:
            # Ensure path is absolute relative to script dir
            if not os.path.isabs(template_path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                full_path = os.path.join(script_dir, template_path)
            else:
                full_path = template_path
                
            # Convert forward slashes to backward slashes for Windows compatibility
            full_path = os.path.normpath(full_path)
            
            if not os.path.exists(full_path):
                print(f"[WARN] Image file not found: {full_path}")
                cls._template_cache_cls[template_path] = None
                return None
                
            tmpl = cv2.imread(full_path, 0)
            if tmpl is None:
                print(f"[WARN] Failed to read image (integrity check): {full_path}")
            cls._template_cache_cls[template_path] = tmpl
            
        return cls._template_cache_cls[template_path]

    def adb_run(self, args, timeout=10, **kwargs):
        return subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)

    def adb_shell(self, shell_cmd, timeout=10):
        return subprocess.run(
            [self.adb_cmd, "-s", self.device_id, "shell", shell_cmd],
            capture_output=True, timeout=timeout)

    def capture_screen(self):
        """Capture screen and load into RAM"""
        sleep(0.3)  # เบรกลดภาระ CPU ไม่ให้วนลูปดึงจอเร็วเกินไป
        try:
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                
            result = subprocess.run(
                [self.adb_cmd, "-s", self.device_id, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=10, **kwargs
            )
            
            if result.returncode == 0 and len(result.stdout) > 100:
                img_array = np.frombuffer(result.stdout, np.uint8)
                self._screen = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)
                self._screen_color = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            else:
                with open(self.filename, "wb") as f:
                    f.write(result.stdout)
                self._screen = cv2.imread(self.filename, 0)
                self._screen_color = cv2.imread(self.filename, cv2.IMREAD_COLOR)
                
            # Global popup checks (like fixnet1.png) - หาตลอดคลุมทั้งการทำงาน!
            if not getattr(self, "_in_popup_check", False):
                self._in_popup_check = True
                try:
                    self.check_floating_popups()
                except Exception as e:
                    print(f"[{self.device_id}] Popup check error: {e}")
                self._in_popup_check = False
                
        except Exception as e:
            print(f"[{self.device_id}] Capture error: {e}")
            if hasattr(self, "_in_popup_check"):
                self._in_popup_check = False

    def _find_in_screen(self, template_path, similarity=0.95):
        """Find template in cached screen image (no new capture)"""
        if self._screen is None:
            return None
        tmpl = self._get_template(template_path)
        if tmpl is None:
            return None
        try:
            result = cv2.matchTemplate(self._screen, tmpl, cv2.TM_CCOEFF_NORMED)
            loc = np.where(result >= similarity)
            if len(loc[0]) > 0:
                y, x = loc[0][0], loc[1][0]
                h, w = tmpl.shape
                return (x + w // 2, y + h // 2)
        except:
            pass
        return None

    def find(self, template_path, similarity=0.95):
        """Capture + find"""
        self.capture_screen()
        return self._find_in_screen(template_path, similarity)

    def exists(self, template_path, similarity=0.95):
        return self.find(template_path, similarity) is not None

    def exists_in_cache(self, template_path, similarity=0.95):
        """Check if template exists in already-captured screen"""
        return self._find_in_screen(template_path, similarity) is not None

    def _get_similarity_score(self, template_path):
        """Get max similarity score for template in cached screen"""
        if self._screen is None:
            return 0.0
        tmpl = self._get_template(template_path)
        if tmpl is None:
            return 0.0
        try:
            result = cv2.matchTemplate(self._screen, tmpl, cv2.TM_CCOEFF_NORMED)
            return float(np.max(result))
        except:
            return 0.0

    def click(self, PSMRL, similarity=0.95):
        target = None
        if isinstance(PSMRL, str):
            if os.path.exists(PSMRL):
                target = self._find_in_screen(PSMRL, similarity)
                if target is None:
                    print(f"[{self.device_id}] Template not found: {PSMRL}")
        elif isinstance(PSMRL, tuple):
            target = PSMRL
            
        if target:
            x, y = target
            self.tap(x, y) # Use the improved tap method
            return True
        return False
    
    def tap(self, x, y):
        """Direct tap without image search - uses a short swipe with random jitter for reliability"""
        import random
        # 1. Faster jitter for multi-process mode
        jitter = random.uniform(0.05, 0.25)
        sleep(0.1 + jitter) 
        
        # 2. Using swipe with 300ms duration for better registration
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "input", "swipe", 
                     str(x), str(y), str(x), str(y), "300"])
        
    def type_text(self, text):
        """Type text via ADB (for search box) - clears it first to avoid double typing"""
        # 1. Clear text (Move to end then send backspaces)
        self.adb_shell("input keyevent 123") # MOVE_END
        for _ in range(3):
            self.adb_shell("input keyevent 67 67 67 67 67 67 67 67 67 67") # 10 backspaces at once
        
        # 2. Type new text
        escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "input", "text", escaped])
        sleep(0.5) # Wait for UI to process text input

    def swipe(self, x1, y1, x2, y2, duration=300):
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "input", "swipe", 
                     str(x1), str(y1), str(x2), str(y2), str(duration)])

    def check_black_screen(self, threshold=0.8):
        """Check if screen is mostly black using mean brightness"""
        if self._screen is None:
            return True  # ถ้า capture ไม่ได้เลย ถือว่าจอดำ
        try:
            mean_brightness = np.mean(self._screen)
            # ถ้าความสว่างเฉลี่ยต่ำกว่า 15 = จอดำ
            return mean_brightness < 15
        except:
            return False

    def check_floating_popups(self):
        """
        Check and click floating popups (fixnetv2 / fixplay / fixnet1).
        เจอก็กด วนเช็คซ้ำจนกว่าจะไม่เจอ popup ใดๆ
        ทำงานทุกรอบ capture_screen() คลุมทั้งไฟล์
        """
        # fixnetv2.png: เจอก็กด แล้วรอกด fixnetv2ok.png
        if self.exists_in_cache("img/fixnetv2.png"):
            print(f"[{self.device_id}] [POPUP] fixnetv2.png detected, clicking...")
            self.click("img/fixnetv2.png")
            sleep(2)
            self._raw_capture()
            if self.exists_in_cache("img/fixnetv2ok.png"):
                self.click("img/fixnetv2ok.png")
                sleep(1)
            return

        if self.exists_in_cache("img/fixplay.png"):
            print(f"[{self.device_id}] [POPUP] fixplay.png detected, clicking...")
            self.click("img/fixplay.png")
            sleep(2)
            # After fixplay, FORCE wait and click check-ok1.png
            print(f"[{self.device_id}] [POPUP] Waiting for check-ok1.png after fixplay...")
            for _ in range(120):  # Wait up to 120 seconds
                self._raw_capture()
                if self.exists_in_cache("img/check-ok1.png"):
                    print(f"[{self.device_id}] [POPUP] check-ok1.png found after fixplay, clicking...")
                    self.click("img/check-ok1.png")
                    sleep(1)
                    break
                sleep(1)

        # fixnet1.png: วนเช็คซ้ำจนกว่าจะไม่เจอ (re-capture ทุกรอบ)
        fixnet1_clicks = 0
        while self.exists_in_cache("img/fixnet1.png", similarity=0.95):
            fixnet1_clicks += 1
            print(f"[{self.device_id}] [POPUP] fixnet1.png detected (click #{fixnet1_clicks}), clicking...")
            self.click("img/fixnet1.png", similarity=0.95)
            sleep(1)
            self._raw_capture()  # จับภาพใหม่เพื่อเช็คซ้ำ (ไม่วนกลับ popup check)
            if fixnet1_clicks >= 10:
                print(f"[{self.device_id}] [POPUP] fixnet1.png clicked 10 times, breaking to avoid infinite loop")
                break

        if self.exists_in_cache("img/fixaccep.png"):
            print(f"[{self.device_id}] [POPUP] fixaccep.png detected, clicking...")
            self.click("img/fixaccep.png")
            sleep(1)

    def _raw_capture(self):
        """Capture screen WITHOUT triggering popup checks (ป้องกันวนซ้อน)"""
        try:
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [self.adb_cmd, "-s", self.device_id, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=10, **kwargs
            )
            if result.returncode == 0 and len(result.stdout) > 100:
                img_array = np.frombuffer(result.stdout, np.uint8)
                self._screen = cv2.imdecode(img_array, cv2.IMREAD_GRAYSCALE)
                self._screen_color = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            else:
                with open(self.filename, "wb") as f:
                    f.write(result.stdout)
                self._screen = cv2.imread(self.filename, 0)
                self._screen_color = cv2.imread(self.filename, cv2.IMREAD_COLOR)
        except Exception as e:
            print(f"[{self.device_id}] Raw capture error: {e}")

    def check_error_images(self, skip_fixcak=False, skip_icon=False):
        """Check error images using cached screen"""

        # ===== FLOATING POPUP CHECKS (กดแล้วทำงานต่อ ไม่ return error) =====
        self.check_floating_popups()
        # ====================================================================

        # fixcak.png: restart process if found
        if not skip_fixcak:
            fixcak_path = "img/fixcak.png"
            if os.path.exists(fixcak_path) and self.exists_in_cache(fixcak_path):
                return "fixcak"
        
        # stopcheck.png: complete/stop process if found
        # Try multiple thresholds like in example code
        for th in [0.95, 0.9, 0.85, 0.8]:
            if self.exists_in_cache("img/stopcheck.png", similarity=th):
                return "stopcheck"
        
        # Common login errors
        if self.exists_in_cache("img/fixbuglogin.png"):
            return "fixbug"
            
        if self.exists_in_cache("img/unkhow.png"):
            return "unkhow"
            
        # App crash check: เช็คว่าแอปยังรันอยู่ไหม (ใช้ pidof แทน icon.png)
        if not skip_icon:
            try:
                pid_result = subprocess.run(
                    [self.adb_cmd, "-s", self.device_id, "shell", "pidof", "com.linecorp.LGRGS"],
                    capture_output=True, text=True, timeout=5
                )
                pid = pid_result.stdout.strip()
                if not pid:
                    return "icon"
            except:
                pass
            
        if self.exists_in_cache("img/kaiby.png"):
            return "kaiby"

        if self.exists_in_cache("img/kaiby1.png"):
            return "kaiby"

        error_images = ["img/failed1.png", "img/fixalerterror1.png"]
        for err in error_images:
            if self.exists_in_cache(err):
                return "error_img"
                
        return None

    # =========================================================
    # OCR Methods - For Gear Mode
    # =========================================================
    def ocr_read_region(self, x, y, w, h):
        """Read text from a specific region of the cached color screen using EasyOCR."""
        if self._screen_color is None or not self.do_gear:
            return []
        
        # Crop region from color image
        img = self._screen_color[y:y+h, x:x+w]
        
        if img is None or img.size == 0:
            print(f"[{self.device_id}] OCR crop region empty!")
            return []
        
        try:
            reader = get_ocr_reader()
            results = reader.readtext(img, detail=1)
            
            text_results = []
            for (bbox, text, conf) in results:
                if conf > 0.3:
                    text_results.append((text, conf))
            
            return text_results
        except Exception as e:
            print(f"[{self.device_id}] [OCR ERROR] OCR failed: {e}")
            print(f"[{self.device_id}] [OCR ERROR] Please install Visual C++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe")
            return []

    def ocr_read_full_screen(self):
        """Read all text from the full cached color screen."""
        if self._screen_color is None or not self.do_gear:
            return ""
        
        region = self.ocr_region
        return self.ocr_read_region(region["x"], region["y"], region["w"], region["h"])

    def check_gear_by_text(self):
        """Check gear by reading text from screen and matching against config gear names."""
        if not self.do_gear:
            return set()
        
        try:
            print(f"[{self.device_id}] Reading screen text with OCR...")
            
            # Capture fresh screen
            self.capture_screen()
            
            # Read text from OCR region
            ocr_results = self.ocr_read_full_screen()
            
            if not ocr_results:
                print(f"[{self.device_id}] OCR returned no results")
                return set()
            
            # Combine all OCR text into one string (lowercase for matching)
            all_text = " ".join([text for text, conf in ocr_results]).lower()
            print(f"[{self.device_id}] OCR Text: {all_text}")
            
            # Match against gear names from config
            found_gears = set()
            for gear_key, gear_data in self.gear_names.items():
                # Support new format: {"ocr": "search text", "name": "custom name"}
                if isinstance(gear_data, dict):
                    ocr_text = gear_data.get("ocr", gear_key)
                    gear_name = gear_data.get("name", gear_key)
                else:
                    ocr_text = gear_data
                    gear_name = gear_data
                
                if ocr_text.lower() in all_text:
                    found_gears.add(gear_name)
                    print(f"[{self.device_id}] Found gear: {gear_name}")
            
            return found_gears
        except Exception as e:
            print(f"[{self.device_id}] [OCR ERROR] check_gear_by_text failed: {e}")
            print(f"[{self.device_id}] [OCR ERROR] Gear scan skipped due to OCR error")
            return set()

    # =========================================================
    # Logic Methods
    # =========================================================
    def clear_specific_shared_prefs(self):
        """Delete ALL shared_prefs and clear app cache"""
        base = "/data/data/com.linecorp.LGRGS/shared_prefs"
        cache_dir = "/data/data/com.linecorp.LGRGS/cache"
        
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.linecorp.LGRGS"])
        sleep(1)
        
        # Total clear including cache (Restore to Full Clear)
        self.adb_shell(f"su -c 'rm -rf {base}/* && rm -rf {cache_dir}/*'")
        print(f"[{self.device_id}] Cleared shared_prefs + cache (Full)")

    def inject_file(self, local_xml_path):
        print(f"[{self.device_id}] Injecting file (Robust Mode)...")
        
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.linecorp.LGRGS"])
        sleep(2)
        
        self.adb_shell("su -c 'killall -9 com.linecorp.LGRGS 2>/dev/null || true'")
        sleep(1)

        src = os.path.abspath(local_xml_path)
        tmp = f"/data/local/tmp/temp_pref_{self.device_id.replace(':','_')}.xml"
        final_dir = "/data/data/com.linecorp.LGRGS/shared_prefs"
        final = f"{final_dir}/_LINE_COCOS_PREF_KEY.xml"
        
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                # Push to tmp
                result = self.adb_run([self.adb_cmd, "-s", self.device_id, "push", src, tmp], timeout=30)
                if result.returncode != 0:
                    err = result.stderr.decode('utf-8', errors='ignore') if result.stderr else 'Unknown Error'
                    print(f"[{self.device_id}] Push attempt {attempt} failed: {err}")
                    sleep(2)
                    continue
                
                # Copy, set permissions and owner (no frail 'wc -c' check)
                shell_cmd = (
                    f"su -c '"
                    f"cp {tmp} {final} && "
                    f"chmod 666 {final} && "
                    f"chown $(stat -c %u:%g {final_dir} 2>/dev/null || stat -c %u:%g {final_dir}/.. 2>/dev/null || echo 1000:1000) {final} || true && "
                    f"rm -f {tmp}"
                    f"'"
                )
                self.adb_shell(shell_cmd)
                
                print(f"[{self.device_id}] Injection successful on attempt {attempt}")
                return local_xml_path
                    
            except Exception as e:
                print(f"[{self.device_id}] Attempt {attempt} error: {e}")
                sleep(2)
        
        print(f"[{self.device_id}] Injection FAILED after {max_retries} attempts!")
        return None

    def first_loop_process(self):
        try:
            print(f"[{self.device_id}] Starting First Loop Process (Turbo Mode)...")
            self.clear_specific_shared_prefs()
            sleep(1.5)
            
            # 1. Ensure we are at Home screen
            self.adb_shell("input keyevent 3")
            sleep(0.5)

            # 2. Sequence 1
            print(f"[{self.device_id}] Processing SEQ 1...")
            res1 = self.process_sequence(self.seq1)
            if res1 == "restart": return "restart"
            if res1 == "complete": return "complete"
            if res1 == "failed": return "failed"
            
            # 3. Back logic - Speed Mode (Triple Back)
            print(f"[{self.device_id}] Back Speed Mode: Executing Triple Back...")
            sleep(1) # Reduced from 4s
            for _ in range(3):
                self.adb_shell("input keyevent 4")
                sleep(0.2)
            sleep(0.5)
            
            # 4. Sequence 2
            print(f"[{self.device_id}] Processing SEQ 2...")
            res2 = self.process_sequence(self.seq2)
            if res2 == "restart": return "restart"
            if res2 == "complete": return "complete"
            if res2 == "failed": return "failed"
            
            # 5. End and Close App
            print(f"[{self.device_id}] First Loop Finished. Clearing app...")
            self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.linecorp.LGRGS"])
            sleep(0.5)
            return "complete"
            
        except Exception as e:
            print(f"[{self.device_id}] First Loop Error: {e}")
            return "error"

    def process_sequence(self, sequence):
        idx = 0
        for item in sequence:
            idx += 1
            # Check for global triggers before each item
            self.capture_screen()
            # Skip icon check if we are currently looking for icon.png in sequence 
            # OR if we are at the very beginning of the sequence (app still launching)
            skip_icon = (item == 'icon.png' or idx <= 3)
            err = self.check_error_images(skip_icon=skip_icon)
            if err == "fixcak": return "restart"
            if err == "icon":
                print(f"[{self.device_id}] App closed/crashed! Relaunching with am start...")
                self.open_app()
                return "restart"
            if err == "stopcheck": return "complete"

            if isinstance(item, tuple):
                print(f"[{self.device_id}] Tapping: {item}")
                self.tap(item[0], item[1])
                sleep(3.5) # Increased to 3.5s for coordinate taps (checkboxes)
                continue
            
            if isinstance(item, str) and item.startswith('@'):
                checkpoint_img = item[1:]
                if not checkpoint_img.startswith('img'):
                    checkpoint_img = f"img/{checkpoint_img}"
                print(f"[{self.device_id}] Checkpoint: waiting for {checkpoint_img} (no click)")
                start_wait = time.time()
                while True:
                    if time.time() - start_wait > 480: # 8 minutes timeout
                        print(f"[{self.device_id}] TIMEOUT waiting for checkpoint {checkpoint_img}. Restarting first_loop...")
                        return "restart"

                    self.capture_screen()
                    
                    # ---- Check floating popups on every iteration ----
                    self.check_floating_popups()
                    # --------------------------------------------------
                    
                    err = self.check_error_images(skip_icon=skip_icon)
                    if err == "fixcak": return "restart"
                    if err == "fixbug":
                        self.click("img/fixbuglogin.png")
                        return "restart"
                    if err == "unkhow":
                        self.click("img/unkhow.png")
                        return "restart"
                    if err == "icon":
                        print(f"[{self.device_id}] App closed/crashed! Relaunching with am start...")
                        self.open_app()
                        return "restart"
                    if err == "stopcheck": return "complete"
                    
                    if self.exists_in_cache(checkpoint_img, similarity=0.95): 
                        print(f"[{self.device_id}] Checkpoint reached: {checkpoint_img}")
                        break
                    sleep(1.5)
                sleep(1.0)
                continue
                
            img_path = f"img/{item}" if isinstance(item, str) and not item.startswith('img') else item
            
            if item == 'icon.png':
                print(f"[{self.device_id}] Opening app via am start (instead of icon click)...")
                self.open_app()
                print(f"[{self.device_id}] App launched, waiting 4s...")
                sleep(4)
                continue

            # === SPECIAL CASE: apple.png ===
            # เจอ apple.png ให้กดด้วย และทำลูป fixid ต่อ
            # เจอ fixid ก่อน -> กด fixok -> refresh -> check -> วนเช็ค fixid ไปเรื่อยๆ
            # ถ้าเจอ fixid ครบ 8 รอบ -> return "failed" ส่งไป login-failed
            # ถ้าไม่เจอ fixid -> ผ่านไปต่อ step ถัดไป
            if item == 'apple.png':
                print(f"[{self.device_id}] Apple step: clicking apple.png (if found) and checking for fixid loop...")
                fixid_count = 0
                max_fixid_retries = 8
                apple_start_wait = time.time()
                
                while True:
                    self.capture_screen()
                    
                    # ---- Check floating popups on every iteration ----
                    self.check_floating_popups()
                    # --------------------------------------------------
                    
                    # Check errors first
                    err = self.check_error_images()
                    if err == "fixcak": return "restart"
                    if err == "fixbug":
                        self.click("img/fixbuglogin.png")
                        return "restart"
                    if err == "unkhow":
                        self.click("img/unkhow.png")
                        return "restart"
                    if err == "icon":
                        print(f"[{self.device_id}] App closed/crashed! Relaunching with am start...")
                        self.open_app()
                        return "restart"
                    if err == "stopcheck": return "complete"
                    
                    # === คลิก apple.png ถ้าเจอ ===
                    if self.exists_in_cache("img/apple.png"):
                        print(f"[{self.device_id}] Found apple.png! Clicking...")
                        self.click("img/apple.png")
                        sleep(2)
                        # ไม่ break นะครับ เพราะต้องเช็ค fixid ต่อ
                    
                    # === fixid1.png → failed ทันที ===
                    if self.exists_in_cache("img/fixid1.png", similarity=0.95):
                        print(f"[{self.device_id}] Found fixid1.png! -> login-failed immediately")
                        return "failed"

                    # === เจอ fixid.png -> เริ่ม loop: fixok -> refresh -> check ===
                    if self.exists_in_cache("img/fixid.png", similarity=0.95):
                        fixid_count += 1
                        print(f"[{self.device_id}] Found fixid.png ({fixid_count}/{max_fixid_retries})")
                        
                        if fixid_count >= max_fixid_retries:
                            print(f"[{self.device_id}] fixid limit reached ({max_fixid_retries} times)! Sending to login-failed...")
                            return "failed"
                        
                        # 1) กด fixok
                        print(f"[{self.device_id}] Step 1: clicking fixok.png...")
                        for _ in range(30):
                            self.capture_screen()
                            if self.exists_in_cache("img/fixok.png"):
                                self.click("img/fixok.png")
                                print(f"[{self.device_id}] Clicked fixok.png")
                                sleep(2)
                                break
                            sleep(1)
                        
                        # 2) กด refresh
                        print(f"[{self.device_id}] Step 2: clicking refresh.png...")
                        for _ in range(30):
                            self.capture_screen()
                            if self.exists_in_cache("img/refresh.png"):
                                self.click("img/refresh.png")
                                print(f"[{self.device_id}] Clicked refresh.png")
                                sleep(3)
                                break
                            sleep(1)
                        
                        # 3) รอ check.png แล้วกด (timeout 60 วิ)
                        print(f"[{self.device_id}] Step 3: waiting for check.png...")
                        check_wait_start = time.time()
                        while time.time() - check_wait_start < 60:
                            self.capture_screen()
                            
                            err2 = self.check_error_images()
                            if err2 == "fixcak": return "restart"
                            if err2 == "fixbug":
                                self.click("img/fixbuglogin.png")
                                return "restart"
                            if err2 == "icon":
                                self.click("img/icon.png")
                                return "restart"
                            if err2 == "stopcheck": return "complete"
                            
                            if self.exists_in_cache("img/check.png"):
                                print(f"[{self.device_id}] Found check.png! Clicking...")
                                self.click("img/check.png")
                                sleep(2)
                                # หลังกด check -> รอดู fixid ก่อน 2 วิ
                                found_fixid_after_check = False
                                for _ in range(2):
                                    self.capture_screen()
                                    if self.exists_in_cache("img/fixid.png"):
                                        print(f"[{self.device_id}] Found fixid.png right after check! Re-routing...")
                                        found_fixid_after_check = True
                                        break
                                    sleep(1)
                                
                                if found_fixid_after_check:
                                    break

                                if self.exists_in_cache("img/fixok.png"):
                                    print(f"[{self.device_id}] Found fixok.png after check! Clicking...")
                                    self.click("img/fixok.png")
                                    sleep(1)
                                break
                            
                            sleep(1)
                        
                        # วนกลับไปเช็ค fixid อีกรอบ
                        continue
                    
                    # === ไม่เจอ fixid และถ้าคลิก apple ไปแล้ว หรือรอสักพักแล้วไม่เจอ fixid -> ผ่านไปได้เลย ===
                    # ตรวจสอบเพิ่มเติมว่าเราข้ามขั้นตอน apple ได้เมื่อไหร่
                    if time.time() - apple_start_wait > 30:
                        print(f"[{self.device_id}] Apple step finished (waited 30s or check passed).")
                        break
                    
                    sleep(1)

                    
                continue  # ไปต่อ item ถัดไปใน sequence

            print(f"[{self.device_id}] Waiting for {item}...")
            start_wait = time.time()
            
            # Custom timeout for specific images
            item_timeout = 480
            if item in ['box6.png', 'end_box.png']:
                item_timeout = 5

            while True:
                if time.time() - start_wait > item_timeout:
                    if item in ['box6.png', 'end_box.png']:
                        print(f"[{self.device_id}] Timeout 5s for {item}, skipping to next step.")
                        break # Continue to next item in sequence
                    print(f"[{self.device_id}] TIMEOUT waiting for {item}. Restarting first_loop...")
                    return "restart"

                # Check fixcak/stopcheck/blackscreen/fixbug/unkhow
                self.capture_screen() # Ensure screen is captured before checking errors
                
                # ---- Check floating popups on every iteration ----
                self.check_floating_popups()
                # --------------------------------------------------
                
                err = self.check_error_images()
                if err == "fixcak":
                    print(f"[{self.device_id}] Found fixcak.png! Restarting first loop...")
                    return "restart"
                if err == "fixbug":
                    print(f"[{self.device_id}] Found fixbuglogin.png! Clicking and restarting...")
                    self.click("img/fixbuglogin.png")
                    return "restart"
                if err == "unkhow":
                    print(f"[{self.device_id}] Found unkhow.png! Clicking and restarting...")
                    self.click("img/unkhow.png")
                    return "restart"
                if err == "icon":
                    print(f"[{self.device_id}] App closed/crashed! Clicking icon to relaunch...")
                    self.click("img/icon.png")
                    return "restart"
                if err == "stopcheck":
                    print(f"[{self.device_id}] Found stopcheck.png! Skipping to complete.")
                    return "complete"
                
                if self.exists_in_cache(img_path):
                    print(f"[{self.device_id}] Found {item}, clicking...")
                    self.click(img_path)
                    sleep(0.8) # Fast transition for images
                    break
                sleep(0.5) # Fast loop search
            
        return "success"

    def wait_and_click_image(self, img_name, timeout=30):
        """Wait for image and click it, return True if found (timeout in seconds)"""
        # Add img/ prefix if not already present
        if not img_name.startswith('img'):
            img_path = f"img/{img_name}"
        else:
            img_path = img_name
        
        start = 0
        while start < timeout:
            try:
                self.capture_screen()
                # ---- Check floating popups on every iteration ----
                self.check_floating_popups()
                # --------------------------------------------------
                if self.exists_in_cache(img_path):
                    print(f"[{self.device_id}] Found {img_name}! Clicking...")
                    self.click(img_path)
                    sleep(0.5)
                    return True
                start += 1
                sleep(1)
            except Exception as e:
                print(f"[{self.device_id}] Error while waiting for {img_name}: {e}")
                
        print(f"[{self.device_id}] Timeout waiting for {img_name} ({timeout}s)")
        return False

    # =========================================================
    # FIND RANGER PROCESS
    # =========================================================
    def process_find_ranger(self, current_file):
        """Process find-ranger sequence - Returns results dict instead of backing up"""
        if not self.do_ranger:
            return {}
        
        print(f"\n[{self.device_id}] === Starting FIND-RANGER Process ===\n")
        
        results = {}
        
        # Step 1 & 2: Navigation to search screen
        print(f"[{self.device_id}] Starting persistent navigation (Searching for sec1/sec2)...")
        sec1_clicked = False
        while True:
            self.capture_screen()

            # ---- Check floating popups ----
            self.check_floating_popups()
            # --------------------------------
            
            # Check for crash while waiting (ใช้ pidof แทน icon.png)
            try:
                pid_result = subprocess.run(
                    [self.adb_cmd, "-s", self.device_id, "shell", "pidof", "com.linecorp.LGRGS"],
                    capture_output=True, text=True, timeout=5
                )
                if not pid_result.stdout.strip():
                    print(f"[{self.device_id}] App crashed, relaunching...")
                    self.open_app()
                    sleep(5)
                    sec1_clicked = False
            except:
                pass

            # Check if we are already at sec2
            if self.exists_in_cache("img/sec2.png"):
                print(f"[{self.device_id}] Reached search screen (sec2), clicking to confirm...")
                self.click("img/sec2.png")
                break
                
            # Try clicking sec1 only once
            if not sec1_clicked and self.exists_in_cache("img/sec1.png"):
                print(f"[{self.device_id}] Found sec1, clicking once then waiting for sec2...")
                self.click("img/sec1.png")
                sec1_clicked = True
                sleep(3) # Initial wait after click
            
            # If nothing found or already clicked sec1, just wait and loop again
            sleep(1.5)
            
        print(f"[{self.device_id}] Reached search screen successfully.")
        sleep(0.5)
        
        # Loop through each character
        for i, character in enumerate(self.characters):
            print(f"\n[{self.device_id}] --- Character {i+1}/{len(self.characters)}: {character} ---")
            
            # a) Tap search box position first
            print(f"[{self.device_id}] Tapping search box (388, 288)")
            self.tap(388, 288)
            sleep(0.3)
            
            # b) Type character name
            print(f"[{self.device_id}] Typing: {character}")
            self.type_text(character)
            sleep(0.5)
            
            # c) Click sec3
            print(f"[{self.device_id}] Clicking sec3.png")
            if not self.wait_and_click_image("sec3.png", timeout=15):
                print(f"[{self.device_id}] Failed to find sec3, skipping character")
                continue
            sleep(0.3)
            
            # d) Click sec4
            print(f"[{self.device_id}] Clicking sec4.png")
            if not self.wait_and_click_image("sec4.png", timeout=15):
                print(f"[{self.device_id}] Failed to find sec4, skipping character")
                continue
            
            # Add longer wait for search results to appear
            print(f"[{self.device_id}] Waiting 2.0s for results...")
            sleep(2.0)
            
            # e) Scan ranger images with RETRY (to be sure)
            current_found_in_iteration = False
            # Revert to full folder scan as requested ("ขอแบบเดิมเลย")
            matching_files = self.ranger_files

            for attempt in range(2):
                if attempt > 0:
                    print(f"[{self.device_id}] Retry scanning ranger (Attempt {attempt+1})...")
                    sleep(1.0)
                    
                self.capture_screen()
                self.check_floating_popups()
                
                for ranger_img in matching_files:
                    ranger_path = f"img/{ranger_img}"
                    # Use very high similarity 0.95 for strict matching (Original images)
                    if self.exists_in_cache(ranger_path, similarity=0.95):
                        # Get base filename
                        file_base = ranger_img.split('/')[-1].replace(".png", "")
                        found_hero_name = file_base
                            
                        # Get folder name from config or default to found_hero_name
                        if isinstance(self.ranger_image_mapping, dict) and ranger_img in self.ranger_image_mapping:
                            data = self.ranger_image_mapping[ranger_img]
                            if isinstance(data, dict):
                                hero_name = data.get("hero", found_hero_name)
                                folder_name = data.get("folder", hero_name)
                            else:
                                hero_name = found_hero_name
                                folder_name = str(data)
                        else:
                            hero_name = found_hero_name
                            folder_name = hero_name
                        
                        results[hero_name] = folder_name
                        current_found_in_iteration = True
                        print(f"[{self.device_id}] Found ranger: {ranger_img} -> hero: {hero_name}, folder: {folder_name}")
                
                if current_found_in_iteration:
                    break # Stop retrying if found
            
            if current_found_in_iteration:
                print(f"[{self.device_id}] Iteration results: {results}")
            else:
                print(f"[{self.device_id}] No rangers found for character: {character}")
            
            # f) Click sec5
            print(f"[{self.device_id}] Clicking sec5.png")
            if not self.wait_and_click_image("sec5.png", timeout=15):
                print(f"[{self.device_id}] Failed to find sec5, continuing")
            sleep(0.3)
            
            # g) Click sec2 again for next character (if not last)
            if i < len(self.characters) - 1:
                if not self.wait_and_click_image("sec2.png", timeout=15):
                    print(f"[{self.device_id}] Failed to find sec2 for next iteration")
                    break
        
        # Print final results
        print(f"\n[{self.device_id}] ========== FIND-RANGER RESULTS ==========")
        print(f"[{self.device_id}] File: {self.current_original_filename}")
        if results:
            for hero_name, folder_name in results.items():
                print(f"[{self.device_id}]   {hero_name} -> {folder_name}")
        else:
            print(f"[{self.device_id}]   No rangers found for any character")
        print(f"[{self.device_id}] ==========================================\n")
        
        # IMPORTANT: Return results instead of backing up
        # The backup will be done in main_login after combining with gear results
        print(f"[{self.device_id}] Find-Ranger complete - NOT clearing app, continuing to gear...")
        return results

    def backup_ranger_results(self, results):
        """Save backup based on find-ranger results"""
        filename = self.current_original_filename or "unknown.xml"
        source_path = "/data/data/com.linecorp.LGRGS/shared_prefs/_LINE_COCOS_PREF_KEY.xml"
        
        # วิธีที่ได้ผล 100%: cp ไป /data/local/tmp → chmod → pull
        safe_dev = self.device_id.replace(":", "_")
        temp_remote = f"/data/local/tmp/backup_{safe_dev}.xml"
        
        try:
            self.adb_shell(f"su -c 'cp {source_path} {temp_remote}'")
            self.adb_shell(f"su -c 'chmod 666 {temp_remote}'")
            
            if results:
                # Build folder name from folder values
                folder_parts = sorted(set(results.values()))
                folder_name = "+".join(folder_parts)
                
                backup_dir = os.path.join("backup-id", folder_name)
                if not os.path.exists(backup_dir):
                    os.makedirs(backup_dir)
                
                dst = os.path.join(backup_dir, filename)
                result = subprocess.run(
                    [self.adb_cmd, '-s', self.device_id, 'pull', temp_remote, dst],
                    capture_output=True, text=True
                )
                
                if result.returncode == 0:
                    print(f"[{self.device_id}] Backed up to: {dst}")
                else:
                    print(f"[{self.device_id}] Backup failed: {result.stderr}")
            else:
                # No results -> not-found
                not_found_dir = "not-found"
                if not os.path.exists(not_found_dir):
                    os.makedirs(not_found_dir)
                
                dst = os.path.join(not_found_dir, filename)
                result = subprocess.run(
                    [self.adb_cmd, '-s', self.device_id, 'pull', temp_remote, dst],
                    capture_output=True, text=True
                )
                
                if result.returncode == 0:
                    print(f"[{self.device_id}] Backed up to not-found: {dst}")
                else:
                    print(f"[{self.device_id}] Backup failed: {result.stderr}")
            
            # Cleanup temp
            self.adb_shell(f"rm -f {temp_remote}")
        except Exception as e:
            print(f"[{self.device_id}] Backup error: {e}")

    # =========================================================
    # CHECK GEAR PROCESS
    # =========================================================
    def process_check_gear(self, current_file, ranger_results=None, skip_findgear1=False):
        """Process check-gear sequence
        
        Args:
            current_file: Current file being processed
            ranger_results: Dict of ranger results to combine with gear results
            skip_findgear1: If True, skip findgear1 and go directly to findgear2 (used when coming from ranger process)
        """
        if not self.do_gear:
            return {}
        
        print(f"\n[{self.device_id}] === Starting CHECK-GEAR Process ===\n")
        
        filename = self.current_original_filename or "unknown_LINE_COCOS_PREF_KEY.xml"
        source_path = "/data/data/com.linecorp.LGRGS/shared_prefs/_LINE_COCOS_PREF_KEY.xml"
        
        # If skip_findgear1 is True, we're coming from ranger and should go directly to findgear2
        if skip_findgear1:
            print(f"[{self.device_id}] Skipping findgear1 (continuing from ranger process)...")
            sleep(1)
        else:
            # Normal flow: click findgear1.png first
            if not self.wait_and_click_image("findgear1.png"):
                print(f"[{self.device_id}] Failed to find findgear1.png")
                return set()
        
        # Click findgear2.png -> findgear3.png
        if not self.wait_and_click_image("findgear2.png"):
            print(f"[{self.device_id}] Failed to find findgear2.png")
            return set()
        
        if not self.wait_and_click_image("findgear3.png"):
            print(f"[{self.device_id}] Failed to find findgear3.png")
            return set()
        
        # Step 2: Read gear names with OCR
        print(f"\n[{self.device_id}] Starting gear OCR check...")
        all_found_gears = set()
        
        # === Attempt 1: checkgear2 -> checkgear3 -> scan ===
        print(f"[{self.device_id}] [GEAR] Attempt 1: checkgear2 -> checkgear3...")
        if not self.wait_and_click_image("checkgear2.png"):
            print(f"[{self.device_id}] Failed to find checkgear2.png")
        
        checkgear3_found = self.wait_and_click_image("checkgear3.png", timeout=15)
        
        if checkgear3_found:
            # checkgear3 สำเร็จ -> สแกน OCR ปกติ
            print(f"[{self.device_id}] [GEAR] checkgear3 found! Scanning OCR...")
            all_found_gears.update(self.check_gear_by_text())
            sleep(2)
            
            # ยังสแกน weapons tabs ต่อตามปกติ
            self.capture_screen()
            self.check_floating_popups()
            if self.exists_in_cache("img/weapons1.png"):
                print(f"\n[{self.device_id}] Checking weapons1 tab...")
                self.click("img/weapons1.png")
                sleep(2)
                all_found_gears.update(self.check_gear_by_text())
                sleep(1)
            
            self.capture_screen()
            self.check_floating_popups()
            if self.exists_in_cache("img/weapons2.png"):
                print(f"\n[{self.device_id}] Checking weapons2 tab...")
                self.click("img/weapons2.png")
                sleep(2)
                all_found_gears.update(self.check_gear_by_text())
                sleep(1)
        else:
            # === checkgear3 ไม่เจอ -> กด weapons1 แล้วสแกน ===
            print(f"[{self.device_id}] [GEAR] checkgear3 NOT found after 15s! Falling back to weapons1...")
            self.capture_screen()
            self.check_floating_popups()
            if self.exists_in_cache("img/weapons1.png"):
                print(f"[{self.device_id}] [GEAR] Clicking weapons1.png and scanning...")
                self.click("img/weapons1.png")
                sleep(2)
                all_found_gears.update(self.check_gear_by_text())
                sleep(1)
            else:
                print(f"[{self.device_id}] [GEAR] weapons1.png not found on screen")
            
            # === Attempt 2: checkgear2 -> checkgear3 อีกรอบ ===
            print(f"\n[{self.device_id}] [GEAR] Attempt 2: checkgear2 -> checkgear3...")
            self.capture_screen()
            self.check_floating_popups()
            if not self.wait_and_click_image("checkgear2.png"):
                print(f"[{self.device_id}] [GEAR] checkgear2 not found on attempt 2")
            
            checkgear3_found_2 = self.wait_and_click_image("checkgear3.png", timeout=15)
            
            if checkgear3_found_2:
                # checkgear3 สำเร็จรอบ 2 -> สแกน OCR
                print(f"[{self.device_id}] [GEAR] checkgear3 found on attempt 2! Scanning OCR...")
                all_found_gears.update(self.check_gear_by_text())
                sleep(2)
            else:
                # === checkgear3 ไม่เจออีก -> กด weapons2 แล้วสแกน ===
                print(f"[{self.device_id}] [GEAR] checkgear3 NOT found again (15s)! Falling back to weapons2...")
                self.capture_screen()
                self.check_floating_popups()
                if self.exists_in_cache("img/weapons2.png"):
                    print(f"[{self.device_id}] [GEAR] Clicking weapons2.png and scanning...")
                    self.click("img/weapons2.png")
                    sleep(2)
                    all_found_gears.update(self.check_gear_by_text())
                    sleep(1)
                else:
                    print(f"[{self.device_id}] [GEAR] weapons2.png not found on screen")
        
        # Return gear results (will be combined with ranger results in main_login)
        print(f"\n[{self.device_id}] Gear results: {all_found_gears if all_found_gears else 'none'}")
        return all_found_gears

    def backup_to_not_found(self, filename, source_path):
        """Backup pref file to not-found folder"""
        not_found_dir = "not-found"
        if not os.path.exists(not_found_dir):
            os.makedirs(not_found_dir)
        
        backup_path = os.path.join(not_found_dir, filename)
        
        # วิธีที่ได้ผล 100%: cp ไป /data/local/tmp → chmod → pull
        safe_dev = self.device_id.replace(":", "_")
        temp_remote = f"/data/local/tmp/backup_{safe_dev}.xml"
        
        try:
            self.adb_shell(f"su -c 'cp {source_path} {temp_remote}'")
            self.adb_shell(f"su -c 'chmod 666 {temp_remote}'")
            result = subprocess.run(
                [self.adb_cmd, '-s', self.device_id, 'pull', temp_remote, backup_path],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"[{self.device_id}] Backed up to not-found: {backup_path}")
            else:
                print(f"[{self.device_id}] Backup failed: {result.stderr}")
            # Cleanup temp
            self.adb_shell(f"rm -f {temp_remote}")
        except Exception as e:
            print(f"[{self.device_id}] Backup error: {e}")

    def clear_and_restart(self):
        """Clear app and prepare for next file"""
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.linecorp.LGRGS"])
        sleep(2)

    # =========================================================
    # Main Login
    # =========================================================
    def main_login(self, current_filename):
        print(f"[{self.device_id}] Starting Main Login...")
        self._login_fixid_count = 0  # Reset fixid counter for each new ID
        
        # Clear app
        self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.linecorp.LGRGS"])
        sleep(2)
        
        # เปิดแอปด้วย am start (เร็วกว่าและเสถียรกว่าคลิก icon.png)
        self.open_app()
        sleep(3)
        
        # === Black Screen Check หลังเปิดแอพ (8 วิ ถ้ายังดำ/เทา → clear + restart) ===
        for black_attempt in range(3):  # ลองได้ 3 ครั้ง
            black_start = time.time()
            is_stuck = False
            while time.time() - black_start < 8:
                self.capture_screen()
                if self._screen is not None:
                    mean_val = float(np.mean(self._screen))
                    if mean_val >= 80:
                        # จอสว่างแล้ว = แอพโหลดสำเร็จ
                        print(f"[{self.device_id}] [BLACK] Screen OK! brightness={mean_val:.0f} (app loaded)")
                        is_stuck = False
                        break
                    else:
                        is_stuck = True
                else:
                    is_stuck = True
                sleep(1)
            
            if is_stuck:
                print(f"[{self.device_id}] [BLACK] Dark screen 8s after launch! (attempt {black_attempt+1}/3) Clearing...")
                self.clear_and_restart()
                self.open_app()
                sleep(3)
            else:
                break  # แอพโหลดสำเร็จ ออกจาก loop
            
        loop_count = 0
        status = "unknown"
        event_passed = False  # หลังเจอ event.png แล้วหยุดเช็ค fixok
        
        while True:
            loop_count += 1
            if loop_count % 5 == 0:
                print(f"[{self.device_id}] Login loop iteration {loop_count}")

            self.capture_screen()

            # === เช็คว่าเกมยังรันอยู่จริงไหม (ทุกรอบ) ===
            try:
                pid_result = subprocess.run(
                    [self.adb_cmd, "-s", self.device_id, "shell", "pidof", "com.linecorp.LGRGS"],
                    capture_output=True, text=True, timeout=5
                )
                if not pid_result.stdout.strip():
                    print(f"[{self.device_id}] [CRASH] App not running! Relaunching...")
                    self.open_app()
                    sleep(5)
                    continue
            except:
                pass

            # ===== FLOATING POPUP CHECKS (กดแล้วทำงานต่อ) =====
            # fixnetv2.png: เจอก็กด แล้วรอกด fixnetv2ok.png
            if self.exists_in_cache("img/fixnetv2.png"):
                print(f"[{self.device_id}] [POPUP] fixnetv2.png detected in login loop, clicking...")
                self.click("img/fixnetv2.png")
                sleep(2)
                self.capture_screen()
                if self.exists_in_cache("img/fixnetv2ok.png"):
                    self.click("img/fixnetv2ok.png")
                    sleep(1)
                continue

            if self.exists_in_cache("img/fixplay.png"):
                print(f"[{self.device_id}] [POPUP] fixplay.png detected in login loop, clicking...")
                self.click("img/fixplay.png")
                sleep(2)
                # Force wait for check-ok1.png after fixplay
                print(f"[{self.device_id}] [POPUP] Waiting for check-ok1.png after fixplay...")
                for _ in range(120):
                    self.capture_screen()
                    if self.exists_in_cache("img/check-ok1.png"):
                        print(f"[{self.device_id}] [POPUP] check-ok1.png found, clicking...")
                        self.click("img/check-ok1.png")
                        sleep(1)
                        break
                    sleep(1)
                continue

            # fixnet1.png: วนเช็คซ้ำจนกว่าจะไม่เจอ (re-capture ทุกรอบ)
            fixnet1_login_clicks = 0
            while self.exists_in_cache("img/fixnet1.png", similarity=0.95):
                fixnet1_login_clicks += 1
                print(f"[{self.device_id}] [POPUP] fixnet1.png detected in login loop (click #{fixnet1_login_clicks}), clicking...")
                self.click("img/fixnet1.png", similarity=0.95)
                sleep(1)
                self.capture_screen()  # จับภาพใหม่เพื่อเช็คซ้ำ
                if fixnet1_login_clicks >= 10:
                    print(f"[{self.device_id}] [POPUP] fixnet1.png clicked 10 times in login, breaking")
                    break
            if fixnet1_login_clicks > 0:
                continue

            # === fixid1.png → failed ทันที ===
            if self.exists_in_cache("img/fixid1.png", similarity=0.95):
                print(f"[{self.device_id}] Found fixid1.png! -> login-failed immediately")
                self._login_fixid_count = 0
                return "failed"

            # === fixid.png Check (เช็คทุกรอบ) -> fixok -> refresh -> check ===
            if self.exists_in_cache("img/fixid.png", similarity=0.95):
                self._login_fixid_count += 1
                print(f"[{self.device_id}] Found fixid.png ({self._login_fixid_count}/8), fixok -> refresh -> check...")
                
                if self._login_fixid_count >= 8:
                    print(f"[{self.device_id}] fixid limit reached (8 times)! Failing...")
                    self._login_fixid_count = 0
                    return "failed"
                
                # 1) กด fixok
                for _ in range(30):
                    self.capture_screen()
                    if self.exists_in_cache("img/fixok.png"):
                        self.click("img/fixok.png")
                        print(f"[{self.device_id}] Clicked fixok.png")
                        sleep(2)
                        break
                    sleep(1)
                
                # 2) กด refresh
                for _ in range(30):
                    self.capture_screen()
                    if self.exists_in_cache("img/refresh.png"):
                        self.click("img/refresh.png")
                        print(f"[{self.device_id}] Clicked refresh.png")
                        sleep(3)
                        break
                    sleep(1)
                
                # 3) รอ check.png แล้วกด
                check_wait_start = time.time()
                while time.time() - check_wait_start < 60:
                    self.capture_screen()
                    if self.exists_in_cache("img/check.png"):
                        print(f"[{self.device_id}] Found check.png! Clicking...")
                        self.click("img/check.png")
                        sleep(2)
                        # หลังกด check -> รอดู fixid ก่อน 2 วิ
                        found_fixid_after_check = False
                        for _ in range(2):
                            self.capture_screen()
                            if self.exists_in_cache("img/fixid.png"):
                                print(f"[{self.device_id}] Found fixid.png right after check! Re-routing...")
                                found_fixid_after_check = True
                                break
                            sleep(1)
                        
                        if found_fixid_after_check:
                            break

                        if self.exists_in_cache("img/fixok.png"):
                            print(f"[{self.device_id}] Found fixok.png after check! Clicking...")
                            self.click("img/fixok.png")
                            sleep(1)
                        break
                    sleep(1)
                
                continue

            # === เจอ refresh.png (ไม่มี fixid) -> กด refresh -> check ===
            if self.exists_in_cache("img/refresh.png"):
                print(f"[{self.device_id}] Found refresh.png (no fixid), clicking refresh -> check...")
                self.click("img/refresh.png")
                sleep(3)
                
                check_wait_start = time.time()
                while time.time() - check_wait_start < 60:
                    self.capture_screen()
                    if self.exists_in_cache("img/check.png"):
                        print(f"[{self.device_id}] Found check.png! Clicking...")
                        self.click("img/check.png")
                        sleep(2)
                        # หลังกด check -> รอดู fixid ก่อน 2 วิ
                        found_fixid_after_check = False
                        for _ in range(2):
                            self.capture_screen()
                            if self.exists_in_cache("img/fixid.png"):
                                print(f"[{self.device_id}] Found fixid.png right after check! Re-routing...")
                                found_fixid_after_check = True
                                break
                            sleep(1)
                        
                        if found_fixid_after_check:
                            break

                        if self.exists_in_cache("img/fixok.png"):
                            print(f"[{self.device_id}] Found fixok.png after check! Clicking...")
                            self.click("img/fixok.png")
                            sleep(1)
                        break
                    sleep(1)
                
                continue
            # ====================================================

            # Crash Check: ใช้ pidof + open_app แทนคลิก icon.png
            try:
                pid_result = subprocess.run(
                    [self.adb_cmd, "-s", self.device_id, "shell", "pidof", "com.linecorp.LGRGS"],
                    capture_output=True, text=True, timeout=5
                )
                if not pid_result.stdout.strip():
                    print(f"[{self.device_id}] App crashed during login. Relaunching...")
                    self.open_app()
                    sleep(5)
                    loop_count = 0
                    continue
            except:
                pass
            
            # fixalerterror1 Check
            if self.exists_in_cache("img/fixalerterror1.png"):
                print(f"[{self.device_id}] Alert error detected. Dimissing...")
                self.click("img/fixalerterror1.png")
                sleep(2)
                loop_count = 0
                continue

            # fixcak.png Check
            if self.exists_in_cache("img/fixcak.png"):
                print(f"[{self.device_id}] Fixcak detected (fix bug login). Dismissing...")
                self.click("img/fixcak.png")
                sleep(2)
                loop_count = 0
                continue
                
            # *** SUCCESS -> Run find-ranger or check-gear ***
            if self.exists_in_cache("img/stoplogin.png"):
                print(f"[{self.device_id}] Login successful! (stoplogin detected)")
                print(f"[{self.device_id}] [DEBUG] Modes -> do_ranger={self.do_ranger}, do_gear={self.do_gear}")
                
                ranger_results = {}
                gear_results = set()
                
                # Run ranger process first if enabled
                if self.do_ranger:
                    print(f"[{self.device_id}] [DEBUG] Starting Ranger scan...")
                    ranger_results = self.process_find_ranger(current_filename)
                    print(f"[{self.device_id}] [DEBUG] Ranger results: {ranger_results}")
                else:
                    print(f"[{self.device_id}] [DEBUG] Ranger scan SKIPPED (do_ranger={self.do_ranger})")
                
                # Then run gear process if enabled
                if self.do_gear:
                    # If both ranger and gear, skip findgear1 since we're already in the app
                    skip_gear1 = self.do_ranger and self.do_gear
                    print(f"[{self.device_id}] [DEBUG] Starting Gear scan (skip_findgear1={skip_gear1})...")
                    gear_results = self.process_check_gear(current_filename, ranger_results, skip_findgear1=skip_gear1)
                    print(f"[{self.device_id}] [DEBUG] Gear results: {gear_results}")
                else:
                    print(f"[{self.device_id}] [DEBUG] Gear scan SKIPPED (do_gear={self.do_gear})")
                
                # Combine results and backup
                filename = self.current_original_filename or "unknown.xml"
                source_path = "/data/data/com.linecorp.LGRGS/shared_prefs/_LINE_COCOS_PREF_KEY.xml"
                
                # Create subfolder name from all found items
                all_names_list = []
                if ranger_results:
                    all_names_list.extend(ranger_results.values())
                if gear_results:
                    all_names_list.extend(gear_results)
                
                found_names = "+".join(sorted(set(all_names_list))) if all_names_list else "unknown"
                print(f"[{self.device_id}] [DEBUG] all_names_list={all_names_list}, found_names={found_names}")
                
                # Determine category folder name
                has_ranger = len(ranger_results) > 0
                has_gear = len(gear_results) > 0
                print(f"[{self.device_id}] [DEBUG] has_ranger={has_ranger}, has_gear={has_gear}")
                
                category = "unknown"
                if has_gear and has_ranger:
                    category = "gear+ranger"
                elif has_gear:
                    category = "gear only"
                elif has_ranger:
                    count = len(ranger_results)
                    category = "ranger" if count == 1 else f"ranger({count})"
                
                print(f"[{self.device_id}] [DEBUG] category={category}")
                
                if category != "unknown":
                    msg = f"[{self.device_id}] 🏆 Success! Found {category}: {found_names}"
                    print(msg)
                    
                    # ALWAYS update hero stats for shared Dashboard (even in CLI mode)
                    ui_stats.update_hero(found_names)
                    
                    # chmod for pull (วิธีที่ได้ผล 100%: cp → tmp → chmod → pull)
                    safe_dev = self.device_id.replace(":", "_")
                    temp_remote = f"/data/local/tmp/backup_{safe_dev}.xml"
                    self.adb_shell(f"su -c 'cp {source_path} {temp_remote}'")
                    self.adb_shell(f"su -c 'chmod 666 {temp_remote}'")
                    
                    # Create backup folder structure: backup-id/category/found_names
                    backup_dir = os.path.join("backup-id", category, found_names)
                    if not os.path.exists(backup_dir):
                        os.makedirs(backup_dir)
                    
                    # Pull file
                    dst = os.path.join(backup_dir, filename)
                    result = subprocess.run(
                        [self.adb_cmd, '-s', self.device_id, 'pull', temp_remote, dst],
                        capture_output=True, text=True
                    )
                    
                    if result.returncode == 0:
                        print(f"[{self.device_id}] ✓ Backed up to: {dst}")
                    else:
                        print(f"[{self.device_id}] ✗ Backup failed: {result.stderr}")
                    # Cleanup temp
                    self.adb_shell(f"rm -f {temp_remote}")
                else:
                    # No results from either ranger or gear -> backup to not-found
                    msg = f"[{self.device_id}] ไม่เจอ Ranger/Gear ที่ต้องการ"
                    print(msg)
                    
                    # ALWAYS update hero stats for shared Dashboard (even in CLI mode)
                    ui_stats.update_hero("ไม่เจอ")
                    
                    print(f"[{self.device_id}] No results from ranger or gear - backing up to not-found")
                    self.backup_to_not_found(filename, source_path)
                
                # Clear app and restart
                self.clear_and_restart()
                return "success"
                
            # Kaiby / Kaiby1 Check (High Priority)
            if self.exists_in_cache("img/kaiby.png") or self.exists_in_cache("img/kaiby1.png"):
                reason = "kaiby1.png" if self.exists_in_cache("img/kaiby1.png") else "kaiby.png"
                print(f"[{self.device_id}] {reason} detected! Stopping login...")
                return "kaiby"

            # Failed
            if self.exists_in_cache("img/login-failed.png"):
                print(f"[{self.device_id}] Login failed (login-failed.png detected)")
                self._login_fixid_count = 0
                return "failed"
                
            # Error/Reset
            error_found = self.check_error_images()
            
            if error_found:
                print(f"[{self.device_id}] Error image found: {error_found}. Resetting...")
                if error_found in ["fixbug", "unkhow"]:
                    img = "img/fixbuglogin.png" if error_found == "fixbug" else "img/unkhow.png"
                    self.click(img)
                    sleep(2)
                self.adb_run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.linecorp.LGRGS"])
                sleep(3)
                self.open_app()
                sleep(5)
                loop_count = 0
                continue
            
            # === fixok.png Check (เช็คตลอด แต่หยุดหลัง event) ===
            if not event_passed and self.exists_in_cache("img/fixok.png"):
                print(f"[{self.device_id}] Found fixok.png! Clicking...")
                self.click("img/fixok.png")
                sleep(1)
                continue

            # Event / Popups
            if self.exists_in_cache("img/event.png"):
                event_passed = True  # หลังจากนี้หยุดเช็ค fixok
                print(f"[{self.device_id}] Event popup detected. Clicking then Back...")
                self.click("img/event.png")
                sleep(1)
                self.adb_shell("input keyevent 4")  # Back button
                sleep(2)
                
                # เช็ค cancel.png เฉพาะหลังเจอ event เท่านั้น
                self.capture_screen()
                if self.exists_in_cache("img/cancel.png"):
                    print(f"[{self.device_id}] Cancel button after event. Clicking...")
                    self.click("img/cancel.png")
                    sleep(1)
                
                loop_count -= 1
                continue
            
            sleep(2)
            if loop_count > 500:
                print(f"[{self.device_id}] Login timeout after 500 iterations")
                status = "timeout"
                return status
        
        return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto Ranger+Gear Script v3.2.0")
    parser.add_argument("--device", type=str, help="Specific device ID/address to run (e.g. 127.0.0.1:5557)")
    parser.add_argument("--no-start", action="store_true", help="Don't auto-start bot threads in GUI")
    parser.add_argument("--no-reset-adb", action="store_true", help="Don't kill/start ADB server")
    parser.add_argument("--cli", action="store_true", help="Launch in Command Line mode (no GUI)")
    parser.add_argument("--minimized", action="store_true", help="Minimize window")
    args = parser.parse_args()

    if args.minimized:
        try:
            import ctypes
            # SW_MINIMIZE = 6 or SW_HIDE = 0. Using 2 (SW_SHOWMINIMIZED) or 6.
            # 2 is show minimized, 0 is hide. Let's use 2 as requested "minimized".
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 2)
        except: pass

    print("=== Auto Ranger+Gear Script v3.2.0 ===")
    
    load_config()
    
    # ลบไฟล์ .lock ทั้งหมดตอนเริ่มรัน (ทั้ง backup/ และ temp/)
    cleanup_count = 0
    # 1. ลบ lock เก่าที่อาจค้างใน backup/
    backup_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup")
    if os.path.exists(backup_folder):
        for lf in glob.glob(os.path.join(backup_folder, "*.lock")):
            try: os.remove(lf); cleanup_count += 1
            except: pass
    # 2. ลบ lock ใน temp/ranger-locks/
    temp_lock_dir = os.path.join(tempfile.gettempdir(), "ranger-locks")
    if os.path.exists(temp_lock_dir):
        for lf in glob.glob(os.path.join(temp_lock_dir, "*.lock")):
            try: os.remove(lf); cleanup_count += 1
            except: pass
    if cleanup_count > 0:
        print(f"[CLEANUP] Removed {cleanup_count} stale .lock file(s)")

    # 3. ลบไฟล์ shared_stats.json เพื่อล้างค่าจากรอบเก่า
    shared_stats_file = ui_stats._get_shared_file()
    if os.path.exists(shared_stats_file):
        try:
            os.remove(shared_stats_file)
            print("[CLEANUP] Removed old shared_stats.json")
        except: pass
    
    # รีเซ็ตค่าในหน่วยความจำด้วย
    ui_stats.success_count = 0
    ui_stats.fail_count = 0
    ui_stats.hero_found_list = {}
    ui_stats.device_statuses = {}
    ui_stats.save_shared()
    
    if not find_adb_executable():
        print("ADB Not Found.")
        sys.exit(1)
    
    # Reset ADB and execute port scan (Skip if requested)
    if not args.no_reset_adb:
        print("[INFO] Connecting to all MuMu ports (ADB Restart inside)...")
        connect_known_ports()
        
    devices = []
    if args.device:
        devices = [args.device]
    else:
        for attempt in range(3):
            devices = get_connected_devices()
            emulator_devices = [d for d in devices if d.startswith("emulator-") or d.startswith("127.0.0.1:")]
            if emulator_devices:
                devices = emulator_devices
                break
            if attempt < 2:
                print(f"[DEV] Attempt {attempt+1}: No devices found yet, waiting 3s...")
                sleep(3)
    
    if not devices:
        print("[ERROR] No devices connected. Make sure your emulator is running.")
        sys.exit(1)

    print(f"[INFO] Connected Devices ({len(devices)}): {', '.join(devices)}")
    
    # Prepare OCR
    find_ranger = config.get("find_ranger", 0)
    find_gear = config.get("find_gear", 0)
    find_all = config.get("find_all", 1)
    if find_gear or find_all:
        print("[INFO] Pre-loading OCR model...")
        try:
            get_ocr_reader()
            print("[OK] OCR model loaded.")
        except Exception as e:
            print(f"[WARN] Failed to load OCR: {e}")
    
    # Setup Queue (Still needed for GUI but threads will use directory scanning)
    source_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backup")
    if os.path.exists(source_folder):
        files = [f for f in os.listdir(source_folder) if f.lower().endswith(".xml")]
        ui_stats.update(total=len(files))
        print(f"[FILE] Found {len(files)} files in {source_folder}")
    
    # Selection
    if not args.cli and GUI_AVAILABLE:
        print(f"{Fore.GREEN}[START] Launching GUI Mode...{Style.RESET_ALL}")
        try:
            ctk.set_appearance_mode("Dark")
            ctk.set_default_color_theme("blue")
            gui = ModernBotGUI(devices, args)
            GUI_INSTANCE = gui
            gui.mainloop()
            sys.exit(0)
        except Exception as e:
            print(f"{Fore.RED}[ERROR] GUI Failed: {e}{Style.RESET_ALL}")
            args.cli = True

    # CLI Mode
    print(f"\n{Fore.CYAN}Starting bot in CLI Mode...{Style.RESET_ALL}")
    
    threads = []
    # If device is specified, only run that one (useful for multi-window mode)
    targets = [args.device] if args.device else devices
    
    print(f"[INFO] Starting {len(targets)} threads...")
    delay = config.get("thread_delay", 5)
    for i, dev in enumerate(targets):
        t = RangerGearBot(dev, args)
        t.start()
        threads.append(t)
        if i < len(targets) - 1:
            sleep(delay)
        
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\n[STOP] Keyboard Interrupt. Stopping...")
    print("\n[DONE] All tasks completed.")