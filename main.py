import cv2
import numpy as np
import subprocess
import os
import time
import shutil
import concurrent.futures
import threading
import queue
import sys

try:
    import customtkinter as ctk
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False
    # Mock ctk objects so class definitions evaluating don't crash the script
    class DummyCTK:
        CTkFrame = object
        CTk = object
    ctk = DummyCTK()

# Configuration
adb_path = "adb"
template_cache = {}

def find_adb_executable():
    global adb_path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    adb_locations = [
        os.path.join(script_dir, "adb", "adb.exe"),
        os.path.join(script_dir, "adb", "adb"),
    ]
    
    adb_locations.append(os.path.join(os.getcwd(), "adb", "adb.exe"))
    
    for loc in adb_locations:
        if not loc.endswith(".exe") and os.name == 'nt' and not os.path.isabs(loc):
             pass
        elif os.path.exists(loc):
            try:
                result = subprocess.run([loc, "version"], capture_output=True, text=True, timeout=5, shell=(os.name == 'nt'))
                if result.returncode == 0:
                    adb_path = loc
                    print(f"[ADB] Verified: {adb_path}")
                    return True
            except:
                pass
                
        if loc == "adb":
            try:
                result = subprocess.run([loc, "version"], capture_output=True, text=True, timeout=5, shell=(os.name == 'nt'))
                if result.returncode == 0:
                    adb_path = loc
                    print(f"[ADB] Verified command: {adb_path}")
                    return True
            except:
                pass
                
    adb_in_path = shutil.which("adb")
    if adb_in_path:
        adb_path = os.path.abspath(adb_in_path)
        print(f"[ADB] Found in PATH: {adb_path}")
        return True
        
    try:
        subprocess.run(["adb", "--version"], capture_output=True, timeout=5, check=True)
        adb_path = "adb"
        print(f"[ADB] Found 'adb' command in system")
        return True
    except:
        pass
        
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
    try:
        subprocess.run([adb_path, "kill-server"], capture_output=True, timeout=3)
        time.sleep(0.1)
        subprocess.run([adb_path, "start-server"], capture_output=True, timeout=3)
        time.sleep(0.5)

        ports = list(range(5555, 5756, 2))
        print(f"\n--- [ADB] Auto-scanning {len(ports)} ports (5555-5755 odd) ---")
        
        connected = []
        def try_connect_port(port):
            try:
                addr = f"127.0.0.1:{port}"
                result = subprocess.run([adb_path, "connect", addr], capture_output=True, timeout=1, text=True)
                out = result.stdout.lower()
                if ("connected" in out or "already connected" in out) and "cannot" not in out:
                    return addr
            except:
                pass
            return None

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
    try:
        result = subprocess.run([adb_path, "devices"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split("\n")[1:]
        raw_devices = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                raw_devices.append(parts[0])
        return raw_devices
    except:
        return []

class LiteBot(threading.Thread):
    def __init__(self, device_id, gui_app=None):
        super().__init__()
        self.device_id = device_id
        self.adb_cmd = adb_path
        self._screen = None
        self.daemon = True
        self.running = True
        self.gui_app = gui_app
        
    def stop(self):
        self.running = False
        print(f"[{self.device_id}] Stop signal received.")
        if self.gui_app:
            self.gui_app.log("INFO", f"[{self.device_id}] Stop signal received.")

    def capture_screen(self):
        time.sleep(0.3)
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
        except Exception as e:
            print(f"[{self.device_id}] Capture error: {e}")

    def _get_template(self, template_path):
        if not os.path.exists(template_path):
            return None
            
        current_mtime = os.path.getmtime(template_path)
        
        # Check cache: Only use cache if the file hasn't been modified
        if template_path in template_cache:
            cached_data = template_cache[template_path]
            if isinstance(cached_data, dict) and cached_data.get('mtime') == current_mtime:
                return cached_data['image']
                
        # Load or reload image if it's new or modified
        tmpl = cv2.imread(template_path, 0)
        template_cache[template_path] = {'image': tmpl, 'mtime': current_mtime}
        return tmpl

    def _find_in_screen(self, template_path, similarity=0.90):
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

    def click(self, tmpl_path, similarity=0.90):
        target = self._find_in_screen(tmpl_path, similarity)
        if target:
            x, y = target
            self.tap(x, y)
            return target
        return None

    def tap(self, x, y):
        import random
        jitter = random.uniform(0.05, 0.25)
        time.sleep(0.1 + jitter)
        subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "input", "swipe", 
                      str(x), str(y), str(x), str(y), "300"], capture_output=True)

    def wait_and_click_image(self, file_name, timeout=30, wait_disappear=False, max_clicks=1):
        img_path = f"img/{file_name}.png"
        print(f"[{self.device_id}] Waiting to click {file_name} ...")
        start_time = time.time()
        last_target = None
        clicked_at_least_once = False
        click_count = 0
        
        while self.running and (timeout is None or time.time() - start_time < timeout):
            self.capture_screen()
            target = self.click(img_path)
            if target:
                click_count += 1
                if max_clicks > 1:
                    print(f"[{self.device_id}] => Clicked {file_name} ({click_count}/{max_clicks})")
                else:
                    print(f"[{self.device_id}] => Clicked {file_name}")

                last_target = target
                clicked_at_least_once = True
                start_time = time.time() # Reset timeout since we found it

                if not wait_disappear and click_count >= max_clicks:
                    return target
                    
                time.sleep(1.5) # Wait a bit for screen to transition after click
            else:
                if clicked_at_least_once:
                    print(f"[{self.device_id}] => {file_name} disappeared, moving next...")
                    return last_target
                time.sleep(1)
                
        print(f"[{self.device_id}] Timeout - did not find {file_name}")
        return None
        
    def ocr_read_region(self, x, y, w, h):
        if not hasattr(self, '_screen_color') or self._screen_color is None:
            return []
            
        img = self._screen_color[y:y+h, x:x+w]
        if img is None or img.size == 0:
            return []
            
        try:
            import easyocr
            # We initialize EasyOCR locally in thread for simplicity. In production might want to cache reader.
            if not hasattr(self.__class__, '_ocr_reader'):
                print(f"[{self.device_id}] Initializing EasyOCR...")
                self.__class__._ocr_reader = easyocr.Reader(['en'], gpu=False)
                
            results = self.__class__._ocr_reader.readtext(img, detail=1)
            text_results = []
            for (bbox, text, conf) in results:
                if conf > 0.3:
                    text_results.append((text, conf))
            return text_results
        except Exception as e:
            print(f"[{self.device_id}] [OCR ERROR] {e}")
            return []

    def run(self):
        import json
        
        while self.running:
            # Load config at the start of each loop so it can be updated live
            target_name = "Kyle"
            try:
                with open("config.json", "r", encoding="utf-8") as f:
                    config = json.load(f)
                    target_name = config.get("target_name", "Kyle")
            except Exception as e:
                print(f"[{self.device_id}] Error loading config.json, using default '{target_name}': {e}")
                
            print(f"\n=========================================")
            print(f"[{self.device_id}] STARTING NEW MAIN LOOP")
            print(f"[{self.device_id}] Target Character for OCR: '{target_name}'")
            print(f"=========================================\n")
            
            # --- COMMENTED OUT FOR TESTING ---
            steps = [
                "icon",
                "login",
                "gust",
                "gust1",
                "gust2",
                "gust3"
            ]
            
            for step in steps:
                # wait_disappear only for gust2 and gust3
                wait_disp = (step in ["gust2", "gust3"])
                self.wait_and_click_image(step, timeout=None, wait_disappear=wait_disp)
                time.sleep(1)
            # ---------------------------------
                
            # Add skipstory with NO TIMEOUT (None means loop forever until found)
            skipstory_steps = [
                "skipstory1", "skipstory2", "skipstory4", "skipstory5", 
                "skipstory6", "skipstory7", "skipstory8","fixstory9",
                "fixstory9v2",
                "skipstory10",
                "skipstory11", "skipstory12", "skipstory13", "skipstory14", "skipstory15",
                "skipstory16", "skipstory17", "skipstory18", "skipstory19", "skipstory20",
                "skipstory21", "skipstory22", "skipstory23", "skipstory24", "skipstory25",
                "skipstory26", "skipstory27", "skipstory28", "skipstory29", "skipstory30",
                "skipstory31", "skipstory32", 
            ]
            
            for step in skipstory_steps:
                # Wait for disappearance for fixstory9v2, skipstory10, skipstory11, skipstory21, skipstory22, skipstory23, skipstory27, skipstory28, skipstory31 and skipstory32
                wait_disp = (step in ["fixstory9v2", "fixstory9v2.png", "skipstory10", "skipstory11", "skipstory21", "skipstory22", "skipstory23", "skipstory27", "skipstory28", "skipstory31", "skipstory32"])
                clicks = 3 if step in ["skipstory29", "skipstory30"] else 1
                step_timeout = 10 if step in ["fixstory9v2", "fixstory9v2.png"] else None
                
                target = self.wait_and_click_image(step, timeout=step_timeout, wait_disappear=wait_disp, max_clicks=clicks)
                time.sleep(1)
                
                # Special case for skipstory2: Wait 15 secs and tap the same position again instead of looking for skipstory3
                if step == "skipstory2" and target:
                    print(f"[{self.device_id}] Delay 15s and tap same position for skipping part 3 ...")
                    time.sleep(15)
                    self.tap(target[0], target[1])
                    time.sleep(1)

            # Clear app (break logic)
            print(f"[{self.device_id}] Closing app com.netmarble.tskgb...")
            subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.netmarble.tskgb"], capture_output=True)
            print(f"[{self.device_id}] App closed successfully.")
            # Breaking out of this "section" to proceed below
            
            # Additional step sequences
            stepp_list = ["icon", "stepp1", "stepp2", "stepp3", "stepp4", "stepp5", "stepp6", "stepp7", "stepp8"]
            for step in stepp_list:
                self.wait_and_click_image(step, timeout=None)
                time.sleep(1)
                
            gacha_list = ["gacha1", "gacha2", "gacha3"]
            for step in gacha_list:
                self.wait_and_click_image(step, timeout=None)
                time.sleep(1)
                
            # Tap 946, 668 13 times
            print(f"[{self.device_id}] Tapping (946, 668) 13 times...")
            for i in range(13):
                self.tap(946, 668)
                time.sleep(0.5)
                
            self.wait_and_click_image("gacha4", timeout=None)
            time.sleep(1)
            
            self.wait_and_click_image("selectgacha", timeout=None)
            time.sleep(1)
            
            self.wait_and_click_image("selectgacha1", timeout=None)
            time.sleep(1)
                
            self.wait_and_click_image("gacha3", timeout=None)
            time.sleep(1)

            print(f"[{self.device_id}] Tapping (946, 668) 13 times for second round...")
            for i in range(13):
                self.tap(946, 668)
                time.sleep(0.5)

            # Scan name via OCR BEFORE gacha4
            print(f"[{self.device_id}] Checking name via OCR for '{target_name}'...")
            self.capture_screen() # Ensure fresh screen
            # Region(70, 74, 1107, 543) -> Wait, 1107x543 is huge,
            # (x, y, w, h)
            text_data = self.ocr_read_region(70, 74, 1107, 543)
            found_target = False
            for text, conf in text_data:
                print(f"[{self.device_id}] OCR Read: '{text}' (conf: {conf:.2f})")
                if target_name.lower() in text.lower():
                    found_target = True
                    break
                    
            if found_target:
                print(f"[{self.device_id}] => SUCCESS! Found target character '{target_name}'.")
                print(f"[{self.device_id}] Stopping bot for this window. Please check the emulator manually.")
                if self.gui_app:
                    self.gui_app.notify_found(self.device_id, target_name)
                return # Stop this specific instance by returning out of the function/thread

            print(f"[{self.device_id}] Target '{target_name}' not found. Continuing process...")

            stepout_list = ["gacha4", "out1", "out2", "out3", "out4", "out5", "out6"]
            
            # Do out sequences
            for step in stepout_list:
                self.wait_and_click_image(step, timeout=None)
                time.sleep(1)
                
            # close app via adb
            print(f"[{self.device_id}] Restarting loop sequence. Closing app...\n")
            subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.netmarble.tskgb"], capture_output=True)
            time.sleep(2)

        print(f"[{self.device_id}] Bot thread stopped gracefully.")

class DeviceMonitorWidget(ctk.CTkFrame):
    def __init__(self, parent, device_id, index, gui_app):
        super().__init__(parent, fg_color="#383838", corner_radius=6, height=32)
        self.device_id = device_id
        self.gui_app = gui_app
        self.pack_propagate(False)
        
        ctk.CTkLabel(self, text=f"#{index}", font=ctk.CTkFont(size=11, weight="bold"), text_color="#ffffff", width=25).pack(side="left", padx=(6, 4))
        ctk.CTkLabel(self, text=device_id, font=ctk.CTkFont(family="Consolas", size=10), text_color="#ccc").pack(side="left", padx=(0, 6))
        
        self.lbl_status = ctk.CTkLabel(self, text="Ready", font=ctk.CTkFont(size=10, weight="bold"), text_color="#aaaaaa", width=60)
        self.lbl_status.pack(side="left", padx=6)
        
        self.btn_stop = ctk.CTkButton(self, text="Stop", width=50, height=20, font=ctk.CTkFont(size=10), fg_color="#e53935", hover_color="#c62828", command=self.stop_device)
        self.btn_stop.pack(side="right", padx=4)
        
        self.btn_start = ctk.CTkButton(self, text="Start", width=50, height=20, font=ctk.CTkFont(size=10), fg_color="#4caf50", hover_color="#388e3c", command=self.start_device)
        self.btn_start.pack(side="right", padx=4)

        self.lbl_found = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=10, weight="bold"), text_color="#f2c94c")
        self.lbl_found.pack(side="right", padx=10)

    def stop_device(self):
        self.gui_app.stop_bot(self.device_id)
        self.update_state("Stopped")
        
    def start_device(self):
        self.lbl_found.configure(text="")
        self.gui_app.start_bot(self.device_id) 
        self.update_state("Running")

    def update_state(self, status):
        color_map = {'Running': "#4caf50", 'Stopped': "#ff9800", 'Error': "#e53935", 'Ready': "#aaaaaa", "Found": "#f2c94c"}
        self.lbl_status.configure(text=status.upper(), text_color=color_map.get(status, "#aaaaaa"))

    def show_found(self, target_name):
        self.lbl_found.configure(text=f"🎯 Found: {target_name}")
        self.update_state("Found")


class ConsoleRedirector:
    def __init__(self, original_stdout, log_queue):
        self.original_stdout = original_stdout
        self.log_queue = log_queue
    
    def write(self, message):
        if self.original_stdout:
            self.original_stdout.write(message)
        if message and message.strip():
            try:
                self.log_queue.put_nowait(message.strip())
            except:
                pass
    
    def flush(self):
        if self.original_stdout:
            self.original_stdout.flush()


class MainGUI(ctk.CTk):
    def __init__(self, devices):
        super().__init__()
        self.title("Seven Knights Bot Control")
        self.geometry("600x450")
        self.devices = devices
        self.bot_threads = {}
        self.device_monitors = {}
        
        self.setup_ui()
        
        self._log_queue = queue.Queue()
        sys.stdout = ConsoleRedirector(sys.__stdout__, self._log_queue)
        
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.after(100, self.process_log_queue)
        
        # Start bots automatically on run based on logic (Disabled by request)
        # self.after(500, self.start_all_bots)

    def setup_ui(self):
        toolbar = ctk.CTkFrame(self, height=40, fg_color="#333333", corner_radius=0)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)
        
        ctk.CTkLabel(toolbar, text=f"   ● ONLINE ({len(self.devices)})", font=ctk.CTkFont(size=12, weight="bold"), text_color="#4caf50").pack(side="left", padx=10)
        ctk.CTkButton(toolbar, text="▶ START ALL", width=80, height=24, fg_color="#4caf50", hover_color="#388e3c", command=self.start_all_bots).pack(side="left", padx=5)
        ctk.CTkButton(toolbar, text="⏹ STOP ALL", width=80, height=24, fg_color="#e53935", hover_color="#c62828", command=self.stop_all_bots).pack(side="left", padx=5)
        
        # Devices section
        dev_frame = ctk.CTkFrame(self, fg_color="#2b2b2b", corner_radius=8)
        dev_frame.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(dev_frame, text="   DEVICES LIST", font=ctk.CTkFont(size=11, weight="bold"), text_color="#cccccc", anchor="w").pack(fill="x", pady=(5,0))
        
        self.dev_scroll = ctk.CTkScrollableFrame(dev_frame, fg_color="transparent", height=150)
        self.dev_scroll.pack(fill="x", expand=True, padx=3, pady=3)
        for i, dev in enumerate(self.devices):
            m = DeviceMonitorWidget(self.dev_scroll, dev, i+1, self)
            m.pack(fill="x", pady=1)
            self.device_monitors[dev] = m

        # Log section
        log_frame = ctk.CTkFrame(self, fg_color="#1e1e1e", corner_radius=6)
        log_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        
        self.log_text = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=10), text_color="#aaaaaa", fg_color="#1e1e1e")
        self.log_text.pack(fill="both", expand=True, padx=2, pady=2)
        self.log_text.configure(state="disabled")

    def log(self, level, message): 
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        line_count = int(self.log_text.index('end-1c').split('.')[0])
        if line_count > 1000:
            self.log_text.delete('1.0', f'{line_count - 500}.0')
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def process_log_queue(self):
        try:
            max_per_tick = 20
            count = 0
            while not self._log_queue.empty() and count < max_per_tick:
                msg = self._log_queue.get_nowait()
                self.log("BOT", msg)
                count += 1
        except:
            pass
        self.after(200, self.process_log_queue)

    def start_bot(self, device_id):
        if device_id in self.bot_threads and self.bot_threads[device_id].is_alive():
            self.log("WARN", f"[{device_id}] Bot is already running!")
            return
        
        bot = LiteBot(device_id, gui_app=self)
        self.bot_threads[device_id] = bot
        bot.start()
        self.log("INFO", f"[{device_id}] Bot started.")
        if device_id in self.device_monitors:
            self.device_monitors[device_id].update_state("Running")

    def stop_bot(self, device_id):
        if device_id in self.bot_threads:
            self.bot_threads[device_id].stop()
            self.log("INFO", f"[{device_id}] Stopping bot...")
        if device_id in self.device_monitors:
            self.device_monitors[device_id].update_state("Stopped")

    def start_all_bots(self):
        for dev in self.devices:
            self.start_bot(dev)
            time.sleep(1) # stagger

    def stop_all_bots(self):
        for dev in self.devices:
            self.stop_bot(dev)

    def on_closing(self):
        self.stop_all_bots()
        self.destroy()
        sys.exit(0)

    def notify_found(self, device_id, target_name):
        def _update():
            if device_id in self.device_monitors:
                self.device_monitors[device_id].show_found(target_name)
        self.after(0, _update)

def main():
    if not find_adb_executable():
        print("[ERR] adb not found")
        return
        
    connect_known_ports()
    devices = get_connected_devices()
    
    if not devices:
        print("No connected devices found!")
        return
        
    print(f"Found devices: {devices}")
    
    if GUI_AVAILABLE:
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        gui = MainGUI(devices)
        gui.mainloop()
    else:
        print("GUI mode unavailable. Running bots in purely terminal mode.")
        bots = []
        for d in devices:
            bot = LiteBot(d)
            bot.start()
            bots.append(bot)
            
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            for b in bots:
                b.stop()

if __name__ == '__main__':
    main()
