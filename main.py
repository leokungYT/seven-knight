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
import requests
import ssl

# Fix SSL certificate error for downloading EasyOCR models
ssl._create_default_https_context = ssl._create_unverified_context

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
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1482703851227447316/8xqw1s_Phg6BsEQAEv1-NuVtvTBhJV0AGV8jHIBGbSdNYPLq-MROe-1YC728MZ0xN-uj"

def send_discord_notification(message, image_path=None):
    try:
        data = {"content": message}
        files = {}
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                response = requests.post(DISCORD_WEBHOOK, data=data, files={"file": f}, timeout=15)
        else:
            response = requests.post(DISCORD_WEBHOOK, json=data, timeout=15)
        
        if response.status_code not in [200, 204]:
            print(f"[Discord] Error: {response.status_code}")
    except Exception as e:
        print(f"[Discord] Exception: {e}")

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
        # result = subprocess.run([adb_path, "devices"], capture_output=True, text=True, timeout=10)
        # lines = result.stdout.strip().split("\n")[1:]
        # raw_devices = []
        # for line in lines:
        #     parts = line.strip().split()
        #     if len(parts) >= 2 and parts[1] == "device":
        #         raw_devices.append(parts[0])
        # return raw_devices
        
        # Optimized version based on user request: Only take emulator-xxxx
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run([adb_path, "devices"], capture_output=True, text=True, timeout=10, **kwargs)
        lines = result.stdout.strip().split("\n")[1:]
        devices = []
        raw_list = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                raw_list.append(parts[0])
        
        # Deduplicate based on port logic
        # emulator-5554 -> port 5555
        # 127.0.0.1:5555 -> port 5555
        port_map = {} # {port: original_id}
        
        for dev_id in raw_list:
            port = None
            if dev_id.startswith("emulator-"):
                try:
                    # Console port N means ADB port is N+1
                    port = int(dev_id.split("-")[1]) + 1
                except: continue
            elif ":" in dev_id:
                try:
                    port = int(dev_id.split(":")[1])
                except: continue
            
            if port:
                # If we have multiple IDs for the same port, 
                # keep 127.0.0.1 style if available, otherwise first found
                if port not in port_map or ":" in dev_id:
                    port_map[port] = dev_id
            else:
                # For non-numeric ports (like serial numbers), just keep them
                if dev_id not in port_map.values():
                    port_map[dev_id] = dev_id

        return list(port_map.values())
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

    def sleep(self, seconds):
        if seconds <= 0: return
        start_time = time.time()
        while self.running and (time.time() - start_time < seconds):
            time.sleep(0.1)

    def capture_screen(self):
        self.sleep(0.3)
        try:
            kwargs = {}
            if os.name == 'nt':
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                
            result = subprocess.run(
                [self.adb_cmd, "-s", self.device_id, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=12, **kwargs
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
        if not self.running: return
        import random
        jitter = random.uniform(0.05, 0.25)
        self.sleep(0.1 + jitter)
        if not self.running: return
        subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "input", "swipe", 
                      str(x), str(y), str(x), str(y), "300"], capture_output=True, 
                      creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))

    def wait_and_click_image(self, file_name, timeout=30, wait_disappear=False, max_clicks=1):
        img_path = f"img/{file_name}.png"
        print(f"[{self.device_id}] Waiting to click {file_name} ...")
        start_time = time.time()
        last_target = None
        clicked_at_least_once = False
        click_count = 0
        
        while self.running and (timeout is None or time.time() - start_time < timeout):
            self.capture_screen()
            
            # --- Auto-Click Global Interruptions ---
            if file_name != "fixdata" and file_name != "icon": 
                if self._find_in_screen("img/fixdata.png"):
                    print(f"[{self.device_id}] ⚠️ Found fixdata.png! Clicking coordinate (632, 514).")
                    self.tap(632, 514)
                    self.sleep(1.5)
                    self.capture_screen() # Refresh screen after clicking
            
            # Auto-Recovery: Check if app is still running while waiting
            if not self.is_app_running():
                print(f"[{self.device_id}] Game crashed while waiting for {file_name}! Restarting...")
                self.open_app()
                self.sleep(5)
                # After restart, try to click icon if we are still far from our target
                if file_name != "icon" and "out" not in file_name:
                    self.wait_and_click_image("icon", timeout=10)
            
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
                    
                self.sleep(1.5) # Wait a bit for screen to transition after click
            else:
                if clicked_at_least_once:
                    print(f"[{self.device_id}] => {file_name} disappeared, moving next...")
                    return last_target
                self.sleep(1)
                
        print(f"[{self.device_id}] Timeout - did not find {file_name}")
        return None
        
    def is_app_running(self):
        try:
            # Check if package is running using pidof
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            result = subprocess.run(
                [self.adb_cmd, "-s", self.device_id, "shell", "pidof", "com.netmarble.tskgb"],
                capture_output=True, text=True, timeout=5, **kwargs
            )
            return result.stdout.strip() != ""
        except:
            return False

    def open_app(self):
        # Open app using monkey command (standard way to launch apps via package name)
        print(f"[{self.device_id}] Launching app com.netmarble.tskgb...")
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "monkey", "-p", "com.netmarble.tskgb", "-c", "android.intent.category.LAUNCHER", "1"], capture_output=True, **kwargs)

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
            target_names = ["Kyle"]
            try:
                with open("config.json", "r", encoding="utf-8") as f:
                    config = json.load(f)
                    # Support both old "target_name" (string) and new "target_names" (list)
                    if "target_names" in config:
                        names = config["target_names"]
                        if isinstance(names, list) and len(names) > 0:
                            target_names = names
                        elif isinstance(names, str):
                            target_names = [names]
                    elif "target_name" in config:
                        target_names = [config["target_name"]]
            except Exception as e:
                print(f"[{self.device_id}] Error loading config.json, using default {target_names}: {e}")
                
            if not self.running: break
            print(f"\n=========================================")
            print(f"[{self.device_id}] STARTING NEW MAIN LOOP")
            print(f"[{self.device_id}] Target Characters for OCR: {target_names}")
            print(f"[{self.device_id}] Force-stopping app before starting new loop...")
            subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.netmarble.tskgb"], capture_output=True, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
            self.sleep(2)

            # Auto-check if app is running (Wait up to 3s), if not start it (start packet)
            is_running = False
            for _ in range(3):
                if not self.running: break
                if self.is_app_running():
                    is_running = True
                    break
                self.sleep(1)

            if not self.running: break
            if not is_running:
                print(f"[{self.device_id}] Game not detected or crashed! Restarting packet...")
                self.open_app()
                # Wait another 3s and check again (total 6s)
                self.sleep(3)
                if not self.running: break
                if not self.is_app_running():
                    print(f"[{self.device_id}] App still not running after 6s. Trying to click 'icon' as fallback...")
                    self.wait_and_click_image("icon", timeout=5)
                else:
                    self.sleep(2) # Finish initialization wait

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
                if not self.running: break
                # wait_disappear only for gust2 and gust3
                wait_disp = (step in ["gust2", "gust3"])
                # Use a specific short timeout for icon so it doesn't hang if already in game
                step_timeout = 5 if step == "icon" else None
                self.wait_and_click_image(step, timeout=step_timeout, wait_disappear=wait_disp)
                self.sleep(1)
            # ---------------------------------
                
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
                if not self.running: break
                # Wait for disappearance for fixstory9v2, skipstory10, skipstory11, skipstory21, skipstory22, skipstory23, skipstory27, skipstory28, skipstory31 and skipstory32
                wait_disp = (step in ["fixstory9v2", "fixstory9v2.png", "skipstory10", "skipstory11", "skipstory21", "skipstory22", "skipstory23", "skipstory27", "skipstory28", "skipstory31", "skipstory32"])
                clicks = 3 if step in ["skipstory29", "skipstory30"] else 1
                
                # Special timeout for fixstory9v2
                step_timeout = 10 if step in ["fixstory9v2", "fixstory9v2.png"] else None
                
                if step == "skipstory16":
                    # Special logic for skipstory16: If not found in 10s, tap once and check again
                    print(f"[{self.device_id}] Waiting for skipstory16 (max 10s before retry)...")
                    target = self.wait_and_click_image(step, timeout=10, wait_disappear=wait_disp, max_clicks=clicks)
                    if not self.running: break
                    if not target:
                        print(f"[{self.device_id}] skipstory16 not found in 10s, tapping center and retrying skipstory16...")
                        self.tap(500, 500) # Tap roughly center of screen
                        self.sleep(1)
                        if not self.running: break
                        target = self.wait_and_click_image(step, timeout=None, wait_disappear=wait_disp, max_clicks=clicks)
                elif step == "skipstory22":
                    print(f"[{self.device_id}] Waiting for skipstory22 (max 10 clicks before fix)...")
                    target = self.wait_and_click_image(step, timeout=30, wait_disappear=False)
                    if target:
                        click_count = 1
                        disappeared = False
                        
                        while self.running and click_count < 10:
                            self.capture_screen()
                            if self.click(f"img/{step}.png"):
                                click_count += 1
                                print(f"[{self.device_id}] => Clicked {step} ({click_count}/10)")
                                self.sleep(1.5)
                            else:
                                print(f"[{self.device_id}] => {step} disappeared, moving next...")
                                disappeared = True
                                break
                        
                        if not disappeared and self.running:
                            print(f"[{self.device_id}] {step} stuck after 10 clicks! Trying skipstory22fix...")
                            self.wait_and_click_image("skipstory22fix", timeout=10)
                            self.sleep(1)
                            print(f"[{self.device_id}] Retrying skipstory22 again...")
                            target = self.wait_and_click_image(step, timeout=None, wait_disappear=True)
                else:
                    target = self.wait_and_click_image(step, timeout=step_timeout, wait_disappear=wait_disp, max_clicks=clicks)
                
                self.sleep(1)
                
                # Special case for skipstory2: Wait 15 secs and tap the same position again instead of looking for skipstory3
                if step == "skipstory2" and target:
                    print(f"[{self.device_id}] Delay 15s and tap same position for skipping part 3 ...")
                    self.sleep(15)
                    self.tap(target[0], target[1])
                    self.sleep(1)

            # Clear app (break logic)
            if not self.running: break
            print(f"[{self.device_id}] Closing app com.netmarble.tskgb...")
            subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.netmarble.tskgb"], capture_output=True, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
            print(f"[{self.device_id}] App closed successfully.")
            # Breaking out of this "section" to proceed below
            
            # Additional step sequences
            stepp_list = ["icon", "stepp1", "stepp2", "stepp3", "stepp4", "stepp5", "stepp6", "stepp7", "stepp8"]
            for step in stepp_list:
                if not self.running: break
                step_timeout = 5 if step == "icon" else None
                self.wait_and_click_image(step, timeout=step_timeout)
                self.sleep(1)
                
            gacha_list = ["gacha1", "gacha2", "gacha3"]
            for step in gacha_list:
                if not self.running: break
                self.wait_and_click_image(step, timeout=None)
                self.sleep(1)
                
            # Tap 946, 668 13 times
            if not self.running: break
            print(f"[{self.device_id}] Tapping (946, 668) 13 times...")
            for i in range(13):
                if not self.running: break
                self.tap(946, 668)
                self.sleep(0.5)
                
            if not self.running: break
            self.wait_and_click_image("gacha4", timeout=None)
            self.sleep(1)
            
            if not self.running: break
            self.wait_and_click_image("selectgacha", timeout=None)
            self.sleep(1)
            
            if not self.running: break
            self.wait_and_click_image("selectgacha1", timeout=None)
            self.sleep(1)
                
            if not self.running: break
            self.wait_and_click_image("gacha3", timeout=None)
            self.sleep(1)

            if not self.running: break
            print(f"[{self.device_id}] Tapping (946, 668) 13 times for second round...")
            for i in range(13):
                if not self.running: break
                self.tap(946, 668)
                self.sleep(0.5)

            # Scan name via OCR BEFORE gacha4
            print(f"[{self.device_id}] Checking name via OCR for targets: {target_names}...")
            self.capture_screen() # Ensure fresh screen
            # Region(70, 74, 1107, 543) -> Wait, 1107x543 is huge,
            # (x, y, w, h)
            text_data = self.ocr_read_region(70, 74, 1107, 543)
            found_target = False
            matched_name = None
            for text, conf in text_data:
                print(f"[{self.device_id}] OCR Read: '{text}' (conf: {conf:.2f})")
                for tname in target_names:
                    if tname.lower() in text.lower():
                        found_target = True
                        matched_name = tname
                        break
                if found_target:
                    break
                    
            if found_target:
                print(f"[{self.device_id}] => SUCCESS! Found target character '{matched_name}'.")
                
                # Notification to Discord
                msg = f"🎯 **SUCCESS! Found target!**\nDevice: `{self.device_id}`\nMatched: **{matched_name}**\nAll targets: {target_names}"
                img_path = f"found_{self.device_id}.png"
                if hasattr(self, '_screen_color') and self._screen_color is not None:
                    cv2.imwrite(img_path, self._screen_color)
                    send_discord_notification(msg, img_path)
                else:
                    send_discord_notification(msg)
                
                print(f"[{self.device_id}] Stopping bot for this window. Please check the emulator manually.")
                if self.gui_app:
                    self.gui_app.notify_found(self.device_id, matched_name)
                return # Stop this specific instance by returning out of the function/thread

            print(f"[{self.device_id}] None of targets {target_names} found. Continuing process...")

            stepout_list = ["gacha4", "out1", "out2", "out3", "out4", "out5", "out6"]
            
            # Do out sequences
            for step in stepout_list:
                if not self.running: break
                self.wait_and_click_image(step, timeout=None)
                if step == "out6":
                    print(f"[{self.device_id}] Clicked out6.png, waiting 10s before clearing app...")
                    self.sleep(10)
                else:
                    self.sleep(1)
                
            # close app via adb
            if not self.running: break
            print(f"[{self.device_id}] Restarting loop sequence. Closing app...\n")
            subprocess.run([self.adb_cmd, "-s", self.device_id, "shell", "am", "force-stop", "com.netmarble.tskgb"], capture_output=True)
            self.sleep(2)

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
        self.geometry("1000x850") # Increased size for better log visibility
        self.devices = devices
        self.bot_threads = {}
        self.device_monitors = {}
        self.log_widgets = {}
        
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
        
        self.lbl_online_count = ctk.CTkLabel(toolbar, text=f"   ● ONLINE ({len(self.devices)})", font=ctk.CTkFont(size=12, weight="bold"), text_color="#4caf50")
        self.lbl_online_count.pack(side="left", padx=10)
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
        
        # Use a Tabview for separate logs
        self.log_tabs = ctk.CTkTabview(log_frame, fg_color="#1e1e1e", segmented_button_fg_color="#333333", segmented_button_selected_color="#4caf50")
        self.log_tabs.pack(fill="both", expand=True, padx=2, pady=2)
        
        # Create Global tab
        self.add_log_tab("Global")
        
        # Create tabs for each device
        for dev in self.devices:
            self.add_log_tab(dev)

        # Bottom Bar
        bottom_bar = ctk.CTkFrame(self, height=32, fg_color="#333333", corner_radius=0)
        bottom_bar.pack(fill="x", side="bottom")
        ctk.CTkButton(bottom_bar, text="🔌 Connect ADB", width=100, height=24, font=ctk.CTkFont(size=11), fg_color="#4caf50", hover_color="#388e3c", command=self.connect_missing_devices).pack(side="left", padx=10, pady=4)
        ctk.CTkLabel(bottom_bar, text="v1.1.0", font=ctk.CTkFont(size=10), text_color="#888888").pack(side="right", padx=10)

    def add_log_tab(self, name):
        # Clean name for tab ID (remove special chars if any, but device IDs should be okay)
        tab = self.log_tabs.add(name)
        log_text = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=13), text_color="#aaaaaa", fg_color="#1e1e1e")
        log_text.pack(fill="both", expand=True, padx=2, pady=2)
        log_text.configure(state="disabled")
        self.log_widgets[name] = log_text

    def connect_missing_devices(self):
        self.log("INFO", "Scanning for missing emulators...")
        connect_known_ports()
        
        current_devices = get_connected_devices()
        new_count = 0
        for dev in current_devices:
            if dev not in self.devices:
                new_count += 1
                self.devices.append(dev)
                
                # Add to UI
                m = DeviceMonitorWidget(self.dev_scroll, dev, len(self.devices), self)
                m.pack(fill="x", pady=1)
                self.device_monitors[dev] = m
                
                # Add log tab
                self.add_log_tab(dev)
                
                self.log("INFO", f"Connected new device: {dev}")
        
        if new_count > 0:
            self.lbl_online_count.configure(text=f"   ● ONLINE ({len(self.devices)})")
            self.log("INFO", f"Found {new_count} new device(s).")
        else:
            self.log("INFO", "No new devices found.")

    def log(self, level, message): 
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        
        # Determine which tab to use based on message prefix like [emulator-5554]
        target_tab = "Global"
        clean_msg = message
        
        if message.strip().startswith("["):
            end_idx = message.find("]")
            if end_idx != -1:
                dev_id = message[1:end_idx]
                if dev_id in self.log_widgets:
                    target_tab = dev_id
                    # We can keep the prefix or remove it. Let's keep it for context.
        
        target_widget = self.log_widgets.get(target_tab, self.log_widgets["Global"])
        
        target_widget.configure(state="normal")
        target_widget.insert("end", f"[{ts}] {clean_msg}\n")
        
        line_count = int(target_widget.index('end-1c').split('.')[0])
        if line_count > 1000:
            target_widget.delete('1.0', f'{line_count - 500}.0')
        
        target_widget.see("end")
        target_widget.configure(state="disabled")

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
            time.sleep(2) # Increased stagger to 2s for stability

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
        
    connect_known_ports() # Enabled to automatically connect to emulators
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
