# main.py

import time
from task import huey, job_10min, send_reply
import redis

track = redis.Redis(host='localhost', port=6379, decode_responses=True)

def schedule_send_reply_jobs(user_id: str):
    ids = {
        "10min": job_10min.schedule(args=(user_id,), delay=10*60),
        "24h":   send_reply.schedule(args=(user_id, "24h"), delay=24* 60*60),
        "3d":    send_reply.schedule(args=(user_id, "3d"),  delay=3 * 86400),
        "7d":    send_reply.schedule(args=(user_id, "7d"),  delay=7 * 86400),
    }

    # Use a single Redis hash per user
    redis_key = f"user:{user_id}:tasks"
    for tag, task in ids.items():
        track.hset(redis_key, tag, task.id)
    # ⏳ Set 10-day expiry to auto-clean old tracking info
    track.expire(redis_key, 10 * 24 * 3600)
    print(f"Scheduled tasks for {user_id}: {[f'{tag}: {t.id}' for tag, t in ids.items()]}")

def cancel_send_reply_job(user_id: str, tag: str):
    redis_key = f"user:{user_id}:tasks"
    tid = track.hget(redis_key, tag)

    if not tid:
        print(f"No pending task for user {user_id} tag={tag}")
        return

    huey.revoke_by_id(tid)
    track.hdel(redis_key, tag)
    print(f"Canceled task {tag} for user {user_id}")
def cancel_all_jobs_for_user(user_id: str):
    """
    Cancels all scheduled jobs for the user and deletes the Redis tracking key.
    """
    redis_key = f"user:{user_id}:tasks"
    task_map = track.hgetall(redis_key)

    if not task_map:
        print(f"No scheduled tasks found for user {user_id}")
        return

    for tag, tid in task_map.items():
        huey.revoke_by_id(tid)
        print(f"Canceled task {tag} ({tid}) for user {user_id}")

    track.delete(redis_key)
    print(f"All tasks canceled and tracking key removed for user {user_id}")

# if __name__ == "__main__":
    # # Schedule 4 jobs for "alice"
    # schedule_send_reply_jobs("alice")

    # # # Wait 5 seconds then cancel the 10-minute job (so it never runs)
    # time.sleep(30)
    # print("Canceling 10min job for alice")
    # cancel_send_reply_job("alice", "10min")
