import os
import io
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import pdfplumber
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from zoneinfo import ZoneInfo


MY_SEARCH_TERM = "ΒΑΞΕΒΑΝΗΣ"
ESKAH_URL = "https://eskah.gr/orismoi-kritwn/"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

def get_calendar_service():
    creds = None

    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


def create_event(service, teams, stadium, match_datetime_str):
    """Υπολογίζει την ώρα προσέλευσης και δημιουργεί το event στο Google Calendar."""

    # 1. Διαβάζουμε το κείμενο και το κάνουμε ώρα
    match_time = datetime.strptime(match_datetime_str, "%d/%m/%Y %H:%M")

    # 2. Λέμε ρητά στην Python ότι αυτή η ώρα αφορά την Ελλάδα (ώστε να βρει μόνη της το σωστό offset π.χ. +03:00)
    athens_tz = ZoneInfo("Europe/Athens")
    match_time = match_time.replace(tzinfo=athens_tz)

    # 3. Υπολογίζουμε ώρα προσέλευσης και ώρα λήξης
    arrival_time = match_time - timedelta(minutes=45)
    end_time = match_time + timedelta(hours=2)

    event = {
        'summary': f'Γραμματεία: {teams}',
        'location': stadium,
        'start': {
            'dateTime': arrival_time.isoformat(),  # Πλέον εξάγει σωστά π.χ. 2026-10-21T20:15:00+03:00
            'timeZone': 'Europe/Athens',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'Europe/Athens',
        },
    }

    created_event = service.events().insert(calendarId='primary', body=event).execute()
    print(f"✅ Προστέθηκε στο Calendar: {teams} (Προσέλευση: {arrival_time.strftime('%H:%M')})")
    print(f"🔗 Link: {created_event.get('htmlLink')}")

def get_latest_pdf_url():
    """Βρίσκει το ενσωματωμένο PDF απευθείας από τη σταθερή σελίδα ορισμών."""
    target_url = "https://eskah.gr/orismoi-kritwn/"
    headers = {"User-Agent": "Mozilla/5.0"}

    print(f"Σάρωση της σελίδας {target_url}...")
    response = requests.get(target_url, headers=headers)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, 'html.parser')

    # Ψάχνουμε για το παράθυρο (iframe ή embed) που περιέχει το PDF
    for element in soup.find_all(['iframe', 'embed', 'object']):
        src = element.get('src') or element.get('data')
        if src and '.pdf' in src.lower():

            # --- Η ΛΥΣΗ ΤΟΥ ΠΡΟΒΛΗΜΑΤΟΣ ---
            # Αν το link χρησιμοποιεί τον pdf-viewer, κρατάμε μόνο το καθαρό URL του PDF
            if '?file=' in src:
                src = src.split('?file=')[-1]

            # Αν το link είναι σχετικό, το κάνουμε πλήρες
            if not src.startswith('http'):
                src = "https://eskah.gr" + src
            return src

    # Εναλλακτικά, μήπως υπάρχει ως απλό link
    for link in soup.find_all('a'):
        href = link.get('href')
        if href and href.lower().endswith('.pdf'):

            if '?file=' in href:
                href = href.split('?file=')[-1]

            if not href.startswith('http'):
                href = "https://eskah.gr" + href
            return href

    return None


def process_assignments():
    """Η κύρια συνάρτηση που συντονίζει τη διαδικασία."""
    print("Εκκίνηση συγχρονισμού...")
    service = get_calendar_service()

    pdf_url = get_latest_pdf_url()

    if not pdf_url:
        print("❌ Δεν βρέθηκε αρχείο PDF στη σελίδα.")
        return

    print(f"📥 Κατέβασμα PDF: {pdf_url}")

    # Προσθέτουμε headers και στο κατέβασμα του αρχείου για να μην μας μπλοκάρει
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    pdf_response = requests.get(pdf_url, headers=headers)
    pdf_response.raise_for_status()

    # Έλεγχος: Κατεβάσαμε όντως PDF ή κάποια άκυρη σελίδα;
    content_type = pdf_response.headers.get('Content-Type', '').lower()
    if 'application/pdf' not in content_type:
        print(f"❌ Προσοχή: Το αρχείο που κατέβηκε δεν είναι PDF (είναι {content_type}).")
        print(f"Το link που βρήκε το πρόγραμμα ήταν: {pdf_url}")
        return

    pdf_file = io.BytesIO(pdf_response.content)

    print("🔍 Σάρωση του PDF για ορισμούς...")
    with pdfplumber.open(pdf_file) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):

            # Εξάγουμε όλα τα δεδομένα σε μορφή πίνακα
            tables = page.extract_tables()

            for table in tables:
                for row in table:
                    # Αν η γραμμή είναι άδεια ή έχει λιγότερες από 9 στήλες, την αγνοούμε
                    if not row or len(row) < 9:
                        continue

                    # Ενώνουμε τα κελιά σε ένα κείμενο για να δούμε αν υπάρχει το όνομά σου
                    row_text = " ".join([str(cell) for cell in row if cell])

                    if MY_SEARCH_TERM in row_text:
                        print(f"\nΒρέθηκε ορισμός (Σελίδα {page_num})!")

                        def clean(cell):
                            return str(cell).replace('\n', ' ').strip() if cell else ""

                        date_str = clean(row[0])
                        time_str = clean(row[1])
                        stadium = clean(row[3])
                        team_a = clean(row[4])
                        team_b = clean(row[5])

                        match_datetime = f"{date_str} {time_str}"
                        teams = f"{team_a} - {team_b}"

                        create_event(service, teams, stadium, match_datetime)

if __name__ == "__main__":
    process_assignments()