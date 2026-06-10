import tkinter as tk
from tkinter import ttk, messagebox
import os
import pickle
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import threading
import re
import traceback
from datetime import datetime, timedelta

SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def app_path(*parts):
    return os.path.join(APP_DIR, *parts)


def log_message(message):
    try:
        with open(app_path('app.log'), 'a', encoding='utf-8') as log_file:
            log_file.write(f"{datetime.now().isoformat()} {message}\n")
    except OSError:
        pass


def find_credentials_file():
    credentials = app_path('credentials.json')
    if os.path.exists(credentials):
        return credentials
    for name in os.listdir(APP_DIR):
        if name.startswith('client_secret') and name.endswith('.json'):
            return app_path(name)
    return credentials


def format_api_error(error):
    if isinstance(error, HttpError):
        try:
            details = error.error_details if hasattr(error, 'error_details') else error.reason
            return f"{error.resp.status} {error.resp.reason}: {details}"
        except Exception:
            return str(error)
    return str(error)


def oauth_blocked_help():
    return (
        "This is normal for a personal app — you do NOT need Google verification.\n\n"
        "Step 1 — Add yourself as a test user:\n"
        "1. Open https://console.cloud.google.com/apis/credentials/consent\n"
        "2. Select project: gmail-cleaner-maria\n"
        "3. Under 'Test users', click + ADD USERS\n"
        "4. Add: maria.aqdas2@gmail.com\n"
        "5. Click Save\n\n"
        "Step 2 — Bypass the warning in your browser:\n"
        "1. Run the app again and sign in with maria.aqdas2@gmail.com\n"
        "2. On the 'Access blocked' page, click Advanced (bottom left)\n"
        "3. Click 'Go to Gmail Cleaner (unsafe)'\n"
        "4. Click Allow\n\n"
        "If you do not see Advanced, the test user was not saved correctly.\n"
        "Delete token.pickle in this folder before trying again."
    )


def build_sender_query(sender):
    sender = sender.strip()
    if re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', sender):
        return f'from:{sender}'
    return f'from:"{sender}"'


def parse_date_input(value):
    value = value.strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value}. Use YYYY-MM-DD.")


def gmail_date(date_value):
    return date_value.strftime('%Y/%m/%d')


def build_search_query(sender, after_date=None, before_date=None):
    parts = [build_sender_query(sender)]
    if after_date:
        parts.append(f'after:{gmail_date(after_date)}')
    if before_date:
        parts.append(f'before:{gmail_date(before_date + timedelta(days=1))}')
    return ' '.join(parts)


class GmailCleanerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gmail Cleaner Pro")
        self.root.geometry("700x560")
        self.root.resizable(True, True)
        
        self.service = None
        self.emails = []
        
        self.auth_in_progress = False
        self.setup_ui()
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after(300, lambda: self.root.attributes('-topmost', False))
        self.try_restore_session()
    
    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        title = ttk.Label(main_frame, text="Gmail Cleaner Pro", font=('Arial', 20, 'bold'))
        title.grid(row=0, column=0, columnspan=3, pady=10)
        
        account_frame = ttk.LabelFrame(main_frame, text="Step 1: Connect Gmail", padding=8)
        account_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.signin_btn = ttk.Button(account_frame, text="Sign in with Google", command=self.start_sign_in)
        self.signin_btn.pack(side=tk.LEFT)
        
        self.account_label = ttk.Label(account_frame, text="Not signed in yet", foreground='orange')
        self.account_label.pack(side=tk.LEFT, padx=12)
        
        ttk.Label(main_frame, text="Sender Email:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.sender_entry = ttk.Entry(main_frame, width=40)
        self.sender_entry.grid(row=2, column=1, pady=5, padx=5)
        
        self.scan_btn = ttk.Button(main_frame, text="Scan Emails", command=self.scan_emails, state='disabled')
        self.scan_btn.grid(row=2, column=2, pady=5, rowspan=2, sticky=tk.N)
        
        ttk.Label(main_frame, text="Time Frame:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.timeframe_var = tk.StringVar(value='Last 30 days')
        self.timeframe_combo = ttk.Combobox(
            main_frame,
            textvariable=self.timeframe_var,
            values=[
                'All time',
                'Last 7 days',
                'Last 30 days',
                'Last 90 days',
                'Last 6 months',
                'Last year',
                'Custom range',
            ],
            state='readonly',
            width=37,
        )
        self.timeframe_combo.grid(row=3, column=1, pady=5, padx=5, sticky=tk.W)
        self.timeframe_combo.bind('<<ComboboxSelected>>', self.on_timeframe_change)
        
        date_frame = ttk.Frame(main_frame)
        date_frame.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=5)
        
        ttk.Label(date_frame, text="From:").pack(side=tk.LEFT)
        self.from_date_entry = ttk.Entry(date_frame, width=12)
        self.from_date_entry.pack(side=tk.LEFT, padx=(5, 15))
        
        ttk.Label(date_frame, text="To:").pack(side=tk.LEFT)
        self.to_date_entry = ttk.Entry(date_frame, width=12)
        self.to_date_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(date_frame, text="(YYYY-MM-DD)", foreground='gray').pack(side=tk.LEFT, padx=5)
        self.on_timeframe_change()
        
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=5, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=10)
        
        list_frame = ttk.Frame(main_frame)
        list_frame.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, height=15)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)
        
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=7, column=0, columnspan=3, pady=10)
        
        self.select_all_btn = ttk.Button(button_frame, text="Select All", command=self.select_all, state='disabled')
        self.select_all_btn.pack(side=tk.LEFT, padx=5)
        
        self.trash_btn = ttk.Button(button_frame, text="Move to Trash", command=self.move_to_trash, state='disabled')
        self.trash_btn.pack(side=tk.LEFT, padx=5)
        
        self.status_label = ttk.Label(main_frame, text="Ready", foreground="gray")
        self.status_label.grid(row=8, column=0, columnspan=3, pady=10)
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(6, weight=1)
    
    def on_timeframe_change(self, _event=None):
        custom = self.timeframe_var.get() == 'Custom range'
        state = 'normal' if custom else 'disabled'
        self.from_date_entry.config(state=state)
        self.to_date_entry.config(state=state)
        
        if custom and not self.from_date_entry.get().strip():
            today = datetime.now().date()
            self.from_date_entry.insert(0, (today - timedelta(days=30)).strftime('%Y-%m-%d'))
            self.to_date_entry.insert(0, today.strftime('%Y-%m-%d'))
    
    def get_date_range(self):
        choice = self.timeframe_var.get()
        today = datetime.now().date()
        
        if choice == 'All time':
            return None, None, choice
        if choice == 'Last 7 days':
            return today - timedelta(days=7), today, choice
        if choice == 'Last 30 days':
            return today - timedelta(days=30), today, choice
        if choice == 'Last 90 days':
            return today - timedelta(days=90), today, choice
        if choice == 'Last 6 months':
            return today - timedelta(days=182), today, choice
        if choice == 'Last year':
            return today - timedelta(days=365), today, choice
        
        from_text = self.from_date_entry.get().strip()
        to_text = self.to_date_entry.get().strip()
        if not from_text or not to_text:
            raise ValueError("Please enter both From and To dates for a custom range.")
        
        after_date = parse_date_input(from_text)
        before_date = parse_date_input(to_text)
        if after_date > before_date:
            raise ValueError("From date must be on or before To date.")
        return after_date, before_date, f"{from_text} to {to_text}"
    
    def show_welcome(self):
        messagebox.showinfo(
            "Get started",
            "1. Click 'Sign in with Google' at the top\n"
            "2. Sign in with maria.aqdas2@gmail.com in your browser\n"
            "3. Enter a sender email and click 'Scan Emails'",
        )
    
    def try_restore_session(self):
        token_path = app_path('token.pickle')
        if not os.path.exists(token_path):
            self.status_label.config(
                text='Click "Sign in with Google" at the top first',
                foreground='blue',
            )
            self.root.after(600, self.show_welcome)
            return
        
        self.status_label.config(text="Restoring Gmail session...", foreground="blue")
        self.progress.start()
        self.signin_btn.config(state='disabled')
        
        def restore():
            try:
                creds = self.load_credentials(refresh_only=True)
                self.service = build('gmail', 'v1', credentials=creds)
                self.root.after(0, self.auth_success)
            except Exception:
                self.root.after(0, self.needs_sign_in)
        
        threading.Thread(target=restore, daemon=True).start()
    
    def needs_sign_in(self):
        self.progress.stop()
        self.signin_btn.config(state='normal')
        self.scan_btn.config(state='disabled')
        self.account_label.config(text="Not signed in", foreground='orange')
        self.status_label.config(
            text='Session expired — click "Sign in with Google" at the top',
            foreground='orange',
        )
    
    def start_sign_in(self):
        if self.auth_in_progress:
            return
        self.auth_in_progress = True
        self.signin_btn.config(state='disabled')
        self.status_label.config(text="Opening browser for Google sign-in...", foreground="blue")
        self.progress.start()
        messagebox.showinfo(
            "Sign in to Gmail",
            "Your browser will open now.\n\n"
            "1. Sign in with maria.aqdas2@gmail.com\n"
            "2. If you see 'Access blocked', click Advanced\n"
            "3. Click 'Go to Gmail Cleaner (unsafe)'\n"
            "4. Click Allow\n\n"
            "Then return to this app.",
        )
        threading.Thread(target=self.authenticate, daemon=True).start()
    
    def load_credentials(self, refresh_only=False):
        token_path = app_path('token.pickle')
        creds = None
        if os.path.exists(token_path):
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)
        
        if creds and creds.valid:
            return creds
        
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, 'wb') as token:
                pickle.dump(creds, token)
            return creds
        
        if refresh_only:
            raise RuntimeError("No valid saved session")
        
        flow = InstalledAppFlow.from_client_secrets_file(find_credentials_file(), SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            prompt='consent',
            access_type='offline',
            authorization_prompt_message=(
                "Gmail Cleaner is waiting for you to sign in in the browser."
            ),
            success_message=(
                "Signed in successfully. "
                "You can close this browser tab and return to Gmail Cleaner."
            ),
        )
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)
        return creds
    
    def authenticate(self):
        try:
            log_message('Starting Google sign-in')
            creds = self.load_credentials()
            self.service = build('gmail', 'v1', credentials=creds)
            log_message('Google sign-in successful')
            self.root.after(0, self.auth_success)
        except Exception as e:
            log_message(f'Google sign-in failed: {e}\n{traceback.format_exc()}')
            self.root.after(0, self.auth_error, format_api_error(e))
        finally:
            self.auth_in_progress = False
    
    def auth_success(self):
        self.progress.stop()
        self.status_label.config(text="Authenticated successfully — you can scan emails now", foreground="green")
        self.account_label.config(text="Signed in", foreground="green")
        self.scan_btn.config(state='normal')
        self.signin_btn.config(state='disabled')
    
    def auth_error(self, error):
        self.progress.stop()
        self.signin_btn.config(state='normal')
        self.status_label.config(text=f"Authentication failed: {error}", foreground="red")
        error_text = str(error).lower()
        if 'access_denied' in error_text or 'verification' in error_text or 'access blocked' in error_text:
            messagebox.showerror("Google sign-in blocked", oauth_blocked_help())
        else:
            messagebox.showerror("Error", f"Failed to authenticate:\n{error}")
    
    def scan_emails(self):
        if not self.service:
            messagebox.showwarning("Warning", "Not connected to Gmail yet. Wait for authentication to finish.")
            return

        sender = self.sender_entry.get().strip()
        if not sender:
            messagebox.showwarning("Warning", "Please enter a sender email address")
            return
        
        try:
            after_date, before_date, timeframe_label = self.get_date_range()
        except ValueError as e:
            messagebox.showwarning("Warning", str(e))
            return
        
        query = build_search_query(sender, after_date, before_date)
        
        self.scan_btn.config(state='disabled')
        self.listbox.delete(0, tk.END)
        self.emails = []
        self.status_label.config(
            text=f"Scanning emails from {sender} ({timeframe_label})...",
            foreground="blue",
        )
        self.progress.start()
        
        def scan():
            try:
                results = self.service.users().messages().list(
                    userId='me',
                    q=query,
                    maxResults=50
                ).execute()
                
                messages = results.get('messages', [])
                fetched = []
                
                for msg in messages:
                    msg_data = self.service.users().messages().get(
                        userId='me',
                        id=msg['id'],
                        format='metadata',
                        metadataHeaders=['Subject', 'Date']
                    ).execute()
                    headers = msg_data.get('payload', {}).get('headers', [])
                    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                    date = next((h['value'] for h in headers if h['name'] == 'Date'), 'No Date')
                    
                    fetched.append({
                        'id': msg['id'],
                        'subject': subject,
                        'date': date,
                        'selected': False
                    })
                
                self.emails = fetched
                self.root.after(0, self.scan_complete, len(fetched), sender, timeframe_label)
            except Exception as e:
                self.root.after(0, self.scan_error, format_api_error(e))
        
        threading.Thread(target=scan, daemon=True).start()
    
    def scan_complete(self, count, sender, timeframe_label):
        self.progress.stop()
        if count == 0:
            self.status_label.config(
                text=f"No emails found from {sender} ({timeframe_label})",
                foreground="orange",
            )
            messagebox.showinfo("Info", f"No emails found from {sender} in {timeframe_label}")
        else:
            self.status_label.config(
                text=f"Found {count} emails from {sender} ({timeframe_label})",
                foreground="green",
            )
            for email in self.emails:
                display_text = f"{email['date'][:20]} - {email['subject'][:60]}"
                self.listbox.insert(tk.END, display_text)
            self.select_all_btn.config(state='normal')
            self.trash_btn.config(state='normal')
        
        self.scan_btn.config(state='normal')
    
    def scan_error(self, error):
        self.progress.stop()
        self.status_label.config(text=f"Scan failed: {error}", foreground="red")
        self.scan_btn.config(state='normal')
        messagebox.showerror("Error", f"Failed to scan emails:\n{error}")
    
    def select_all(self):
        self.listbox.select_set(0, tk.END)
        for email in self.emails:
            email['selected'] = True
        self.status_label.config(text=f"Selected {len(self.emails)} emails", foreground="blue")
    
    def move_to_trash(self):
        selected_indices = self.listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Warning", "No emails selected")
            return
        
        selected_emails = [self.emails[i] for i in selected_indices]
        
        if not messagebox.askyesno("Confirm", f"Move {len(selected_emails)} emails to trash?"):
            return
        
        self.trash_btn.config(state='disabled')
        self.select_all_btn.config(state='disabled')
        self.status_label.config(text=f"Moving {len(selected_emails)} emails to trash...", foreground="blue")
        self.progress.start()
        
        def trash():
            try:
                count = 0
                for email in selected_emails:
                    self.service.users().messages().trash(userId='me', id=email['id']).execute()
                    count += 1
                self.root.after(0, self.trash_complete, count)
            except Exception as e:
                self.root.after(0, self.trash_error, str(e))
        
        threading.Thread(target=trash, daemon=True).start()
    
    def trash_complete(self, count):
        self.progress.stop()
        self.status_label.config(text=f"Moved {count} emails to trash", foreground="green")
        messagebox.showinfo("Success", f"Moved {count} emails to trash")
        self.scan_btn.config(state='normal')
        self.trash_btn.config(state='disabled')
        self.select_all_btn.config(state='disabled')
        self.listbox.delete(0, tk.END)
        self.emails = []
    
    def trash_error(self, error):
        self.progress.stop()
        self.status_label.config(text=f"Failed: {error}", foreground="red")
        self.trash_btn.config(state='normal')
        self.select_all_btn.config(state='normal')
        messagebox.showerror("Error", f"Failed to move emails:\n{error}")

if __name__ == "__main__":
    root = tk.Tk()
    app = GmailCleanerApp(root)
    root.mainloop()
