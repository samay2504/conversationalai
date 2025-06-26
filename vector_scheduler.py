"""
vector_scheduler_debug_verbose.py
───────────────────────────────────────────────────────────────
• Pulls rows from a Google Sheet
• Breaks them into token‑capped batches
• Sends each batch to Google Gen‑AI embeddings
• Writes to a temporary FAISS index, then atomically swaps with main index

Enhanced with:
- Atomic index swapping using temporary directory
- Configurable pipeline parameters
- Robust error handling and cleanup
- Zero-downtime updates
"""

# ── 1. Imports & basic config ────────────────────────────────────────────────
import os, time, asyncio, logging, traceback, shutil
from collections import deque
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from filelock import FileLock

# LangChain bits
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings

# Your project helpers
from spreadsheet import get_sheet_values
from error_logger import log_to_openobserve

# ── 2. Environment variables & constants ─────────────────────────────────────
load_dotenv()  # load .env if present

# Default configuration - can be overridden via function parameters
DEFAULT_CONFIG = {
    'sheet_name': os.getenv("SHEET_NAME_NASHERMILES_PRODUCTS", "Sheet1"),
    'sheet_id': os.getenv("SHEET_ID"),
    'model_name': "models/text-embedding-004",
    'token_limit': 2_000,
    'max_batches_per_min': 100,
    'max_concurrent_tasks': 10,
    'faiss_dir': "faiss_indices",
    'index_name': "nashermiles",
    'log_file': "vector_scheduler_errors_nashermiles.log"
}

# ── 3. Enhanced path management ──────────────────────────────────────────────
def get_index_paths(faiss_dir: str, index_name: str):
    """Generate all required paths for index management."""
    faiss_path = Path(faiss_dir)
    return {
        'faiss_dir': faiss_path,
        'main_index': faiss_path / index_name,
        'temp_index': faiss_path / f"{index_name}_new",
        'backup_index': faiss_path / f"{index_name}_backup",
        'lock_file': f"faiss_index_{index_name}.lock",
        'swap_lock_file': f"faiss_index_{index_name}_swap.lock"
    }

def ensure_directories(paths: dict):
    """Create all necessary directories if they don't exist."""
    try:
        paths['faiss_dir'].mkdir(parents=True, exist_ok=True)
        print(f"### DEBUG: 📁 Ensured directory exists: {paths['faiss_dir']}")
    except Exception as e:
        print(f"### DEBUG: ❌ Failed to create directory {paths['faiss_dir']}: {e}")
        raise

def cleanup_temp_index(temp_path: Path):
    """Clean up temporary index directory if it exists."""
    try:
        if temp_path.exists():
            shutil.rmtree(temp_path)
            print(f"### DEBUG: 🧹 Cleaned up temporary index: {temp_path}")
    except Exception as e:
        print(f"### DEBUG: ⚠️  Warning: Failed to cleanup temp index {temp_path}: {e}")

def atomic_index_swap(paths: dict):
    """
    Atomically swap the temporary index with the main index.
    Uses file locking to prevent concurrent access during swap.
    """
    print("### DEBUG: 🔄 Starting atomic index swap...")
    
    with FileLock(paths['swap_lock_file'], timeout=300):  # 5-minute timeout
        try:
            # Step 1: Create backup of current index if it exists
            if paths['main_index'].exists():
                if paths['backup_index'].exists():
                    shutil.rmtree(paths['backup_index'])
                shutil.move(str(paths['main_index']), str(paths['backup_index']))
                print(f"### DEBUG:    ↳ Current index backed up to: {paths['backup_index']}")
            
            # Step 2: Move temporary index to main location
            if paths['temp_index'].exists():
                shutil.move(str(paths['temp_index']), str(paths['main_index']))
                print(f"### DEBUG:    ↳ New index moved to: {paths['main_index']}")
            else:
                raise FileNotFoundError(f"Temporary index not found: {paths['temp_index']}")
            
            # Step 3: Clean up backup (optional - keep for safety)
            # if paths['backup_index'].exists():
            #     shutil.rmtree(paths['backup_index'])
            
            print("### DEBUG: ✅ Atomic index swap completed successfully")
            
        except Exception as e:
            print(f"### DEBUG: ❌ Error during index swap: {e}")
            # Attempt to restore from backup
            try:
                if paths['backup_index'].exists() and not paths['main_index'].exists():
                    shutil.move(str(paths['backup_index']), str(paths['main_index']))
                    print("### DEBUG:    ↳ Restored from backup")
            except Exception as restore_error:
                print(f"### DEBUG: ❌ Failed to restore from backup: {restore_error}")
            raise

# ── 4. File‑based logging setup ─────────────────────────────────────────────
def setup_logging(log_file: str):
    """Initialize logging configuration."""
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        filemode='a'
    )

# ── 5. Sliding‑window AsyncRateLimiter class ────────────────────────────────
class AsyncRateLimiter:
    """
    Ensures we never exceed max_calls in any rolling time window.
    Thread-safe for concurrent async operations.
    """
    def __init__(self, max_calls: int, period: float):
        self.max = max_calls
        self.period = period
        self.calls = deque()
        self.lock = asyncio.Lock()

    async def acquire(self):
        """Wait until there is room in the current time window."""
        async with self.lock:
            now = time.time()

            # Remove old timestamps
            while self.calls and now - self.calls[0] > self.period:
                self.calls.popleft()

            # Wait if quota exceeded
            if len(self.calls) >= self.max:
                wait_for = self.period - (now - self.calls[0])
                print(f"### DEBUG: ⏳ Rate limit hit → sleeping {wait_for:.2f}s")
                await asyncio.sleep(wait_for)

                # Clean up again after sleep
                now = time.time()
                while self.calls and now - self.calls[0] > self.period:
                    self.calls.popleft()

            # Record this call
            self.calls.append(time.time())

# ── 6. Helper utilities ─────────────────────────────────────────────────────
def log_exc(e: Exception, log_file: str = None):
    """Send full traceback to the log file."""
    if log_file:
        setup_logging(log_file)
    logging.error(traceback.format_exc())

def fetch_sheet_to_df(sheet_name: str, sheet_id: str) -> pd.DataFrame:
    """Load Google‑Sheet rows into a Pandas DataFrame."""
    try:
        values = get_sheet_values(sheet_name, sheet_id=sheet_id)
        if not values or len(values) < 2:
            return pd.DataFrame()
        df = pd.DataFrame(values[1:], columns=values[0]).fillna("N/A")
        return df
    except Exception as e:
        print(f"### DEBUG: ❌ Failed to fetch sheet data: {e}")
        return pd.DataFrame()

def row_to_text(row: pd.Series) -> str:
    """Convert a DataFrame row to a single string."""
    return " ".join(f"{col}: {row[col]}" for col in row.index)

def batch_rows(df: pd.DataFrame, token_limit: int) -> list[list[str]]:
    """Build batches where the sum of token counts ≤ token_limit."""
    print(f"### DEBUG: 🗂️  Batching {len(df)} rows with limit {token_limit}...")
    batches, cur_batch, cur_tok = [], [], 0
    
    for _, row in df.iterrows():
        text = row_to_text(row)
        tok = len(text.split())
        
        if tok > token_limit:
            print(f"### DEBUG:    ↳ Skipping oversize row ({tok} tokens)")
            continue

        if cur_tok + tok > token_limit:
            if cur_batch:  # Don't add empty batches
                batches.append(cur_batch)
            cur_batch, cur_tok = [], 0

        cur_batch.append(text)
        cur_tok += tok

    if cur_batch:
        batches.append(cur_batch)

    print(f"### DEBUG: ✅ Created {len(batches)} batches")
    return batches

# ── 7. Worker coroutine ──────────────────────────────────────────────────────
async def embed_one_batch(batch_id: int,
                          texts: list[str],
                          embeddings,
                          limiter: AsyncRateLimiter,
                          log_file: str):
    """Process one batch of texts through the embedding model."""
    print(f"### DEBUG: Batch {batch_id:>3}: waiting for rate‑limit slot…")
    await limiter.acquire()

    print(f"### DEBUG: Batch {batch_id:>3}: 🚀 sending {len(texts)} rows to model")
    try:
        vectors = await asyncio.to_thread(embeddings.embed_documents, texts)
        print(f"### DEBUG: Batch {batch_id:>3}: ✅ got {len(vectors)} vectors")
        return texts
    except Exception as e:
        log_exc(e, log_file)
        print(f"### DEBUG: Batch {batch_id:>3}: ❌ failed (see log)")
        return None

# ── 8. Main configurable pipeline ───────────────────────────────────────────
async def rag_pipeline(sheet_name: str = None,
                      sheet_id: str = None,
                      model_name: str = None,
                      token_limit: int = None,
                      max_batches_per_min: int = None,
                      max_concurrent_tasks: int = None,
                      faiss_dir: str = None,
                      index_name: str = None,
                      log_file: str = None,
                      **kwargs):
    """
    Configurable RAG pipeline with atomic index swapping.
    
    Args:
        sheet_name: Name of the Google Sheet tab
        sheet_id: Google Sheet ID
        model_name: Embedding model name
        token_limit: Maximum tokens per batch
        max_batches_per_min: API rate limit
        max_concurrent_tasks: Concurrency limit
        faiss_dir: Directory for FAISS indices
        index_name: Name of the index
        log_file: Error log file path
        **kwargs: Additional configuration options
    """
    # Merge config with defaults
    config = DEFAULT_CONFIG.copy()
    config.update({k: v for k, v in locals().items() if v is not None and k != 'kwargs'})
    config.update(kwargs)
    
    print("─────────────────────────────────────────────────────────")
    print("### DEBUG: ⏩ Starting Enhanced Vector Scheduler…")
    print(f"### DEBUG: 📋 Config: {config}")

    # Setup paths and logging
    paths = get_index_paths(config['faiss_dir'], config['index_name'])
    setup_logging(config['log_file'])
    
    try:
        # Ensure directories exist
        ensure_directories(paths)
        
        # Clean up any existing temporary index
        cleanup_temp_index(paths['temp_index'])

        # Step 1 – Pull Sheet
        df = fetch_sheet_to_df(config['sheet_name'], config['sheet_id'])
        if df.empty:
            print("### DEBUG: Sheet is empty or fetch failed. Exiting.")
            return False
        print(f"### DEBUG: Sheet rows fetched: {len(df)}")

        # Step 2 – Build batches
        batches = batch_rows(df, config['token_limit'])
        if not batches:
            print("### DEBUG: No valid batches created. Exiting.")
            return False

        # Step 3 – Setup embeddings and concurrency controls
        embeddings = GoogleGenerativeAIEmbeddings(model=config['model_name'])
        limiter = AsyncRateLimiter(config['max_batches_per_min'], 60)
        semaphore = asyncio.Semaphore(config['max_concurrent_tasks'])

        # Step 4 – Process batches concurrently
        async def guarded_worker(batch_id, texts):
            async with semaphore:
                return await embed_one_batch(batch_id, texts, embeddings, limiter, config['log_file'])

        tasks = [guarded_worker(i, b) for i, b in enumerate(batches, 1)]
        results = [r for r in await asyncio.gather(*tasks, return_exceptions=True) 
                  if r is not None and not isinstance(r, Exception)]

        # Step 5 – Collect all texts
        all_texts = [txt for batch in results for txt in batch]
        print(f"### DEBUG: 📝 Total texts to store in FAISS: {len(all_texts)}")

        if not all_texts:
            print("### DEBUG: No texts embedded successfully; aborting.")
            return False

        # Step 6 – Create temporary index
        print("### DEBUG: 💾 Creating temporary FAISS index…")
        try:
            with FileLock(paths['lock_file'], timeout=300):
                # Always create a fresh temporary index
                vs = FAISS.from_texts(all_texts, embeddings)
                vs.save_local(str(paths['temp_index']))
                print(f"### DEBUG:    ↳ Temporary index created at: {paths['temp_index']}")

        except Exception as e:
            log_exc(e, config['log_file'])
            print("### DEBUG: ❌ Error creating temporary index (see log)")
            cleanup_temp_index(paths['temp_index'])
            return False

        # Step 7 – Atomic swap
        try:
            atomic_index_swap(paths)
            print("### DEBUG: ✅ Index update completed successfully")
            return True
            
        except Exception as e:
            log_exc(e, config['log_file'])
            print("### DEBUG: ❌ Error during index swap (see log)")
            cleanup_temp_index(paths['temp_index'])
            
            # Log to external service if available
            try:
                await log_to_openobserve("vector_scheduler_enhanced",
                                   traceback.format_exc(), level="critical")
            except Exception:
                pass
            return False

    except Exception as e:
        log_exc(e, config['log_file'])
        print(f"### DEBUG: ❌ Pipeline failed with error: {e}")
        cleanup_temp_index(paths['temp_index'])
        return False
        
    finally:
        print("### DEBUG: 🎉 Pipeline finished.")
        print("─────────────────────────────────────────────────────────")

# ── 9. Convenience functions ────────────────────────────────────────────────
def start_pipeline(**config):
    """
    Convenience function to start the pipeline with custom configuration.
    
    Example usage:
        start_pipeline(
            sheet_name="Products",
            token_limit=1500,
            max_concurrent_tasks=5
        )
    """
    start_time = time.time()
    success = asyncio.run(rag_pipeline(**config))
    end_time = time.time()
    
    print(f"--- Pipeline completed in {end_time - start_time:.2f} seconds ---")
    print(f"--- Status: {'SUCCESS' if success else 'FAILED'} ---")
    return success

def start_pipeline_with_defaults():
    """Start pipeline with default configuration."""
    return start_pipeline()

# ── 10. Entrypoint ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Example of different ways to run the pipeline
    
    # Option 1: Use defaults
    success = start_pipeline_with_defaults()
#     DEFAULT_CONFIG = {
#     'sheet_name': os.getenv("SHEET_NAME_NASHERMILES_PRODUCTS", "Sheet1"),
#     'sheet_id': os.getenv("SHEET_ID"),
#     'model_name': "models/text-embedding-004",
#     'token_limit': 2_000,
#     'max_batches_per_min': 100,
#     'max_concurrent_tasks': 10,
#     'faiss_dir': "faiss_indices",
#     'index_name': "nashermiles",
#     'log_file': "vector_scheduler_errors_nashermiles.log"
# }
    # Option 2: Custom configuration
    # success = start_pipeline(
    #     sheet_name="FAQ",
    #     token_limit=DEFAULT_CONFIG['token_limit'],
    #     max_concurrent_tasks=DEFAULT_CONFIG['max_concurrent_tasks'],
    #     index_name="faq",
    #     log_file=DEFAULT_CONFIG['log_file'],
    #     sheet_id=DEFAULT_CONFIG['sheet_id'],
    #     model_name=DEFAULT_CONFIG['model_name'],
    #     faiss_dir=DEFAULT_CONFIG['faiss_dir']

    # )
    
    exit(0 if success else 1)