import schedule
import time
import asyncio
import traceback
import logging
# from sending_reminders import send_reminders
# from checking_visit_status import check_visits_and_send_messages
from insta_api import reset_and_store_user_count

from insta_api import refresh_access_token
from visit_scheduler import send_scheduled_reminders,process_daily_leads
from datetime import date, timedelta

# Set up logging
logging.basicConfig(
    filename="scheduler.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# def job():
#     try:
#         asyncio.run(send_reminders())
#         logging.info("Reminder messages sent successfully.")
#     except Exception as e:
#         logging.error(traceback.format_exc())

def job1():
    try:
        reset_and_store_user_count()
        logging.info("User count reset and stored successfully.")
    except Exception as e:
        logging.error(traceback.format_exc())

# def job2():
#     try:
#         # Run the async function using asyncio.run
#         asyncio.run(check_visits_and_send_messages())
#         logging.info("Visit status checked and messages sent successfully.")
#     except Exception as e:
#         logging.error(traceback.format_exc())
def job2():
    try:
        asyncio.run(send_scheduled_reminders())
        logging.info("Job run successfully.")
    except Exception as e:
        logging.error(traceback.format_exc())

# def job4():
#     try:
#         asyncio.run(refresh_access_token())
#         logging.info("Access token refreshed successfully.")
#     except Exception as e:
#         logging.error(traceback.format_exc())


# Schedule the jobs
# schedule.every().day.at("06:00").do(job)
# schedule.every().day.at("22:25").do(job2)
schedule.every().day.at("00:00").do(job1)
# Schedule the job to run every 10 minutes
schedule.every(7).minutes.do(job2)
# Schedule the metrics analysis and sheet updates to run every 24 hours at midnight

schedule.every().day.at("23:45").do(process_daily_leads)
schedule.every().day.at("06:00").do(job2)

if __name__ == "__main__":
    
    print("Scheduler started. Waiting for scheduled times...")
    while True:
        idle_time = schedule.idle_seconds()
        print(idle_time)
        if idle_time is None:
            break  # No more jobs scheduled
        elif idle_time > 0:
            time.sleep(idle_time)
        schedule.run_pending()