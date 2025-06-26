import os
from typing import List, Dict, Any
import logging
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from dotenv import load_dotenv
import traceback
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.prompts import PromptTemplate
from langchain.chains import RetrievalQA
from spreadsheet import get_sheet_values
# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
os.environ["LANGSMITH_API_KEY"] = "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"
os.environ["LANGSMITH_PROJECT"] = "pr-dear-octagon-15"
def get_sheet_as_text(sheet_name: str, exclude_columns: list = None) -> List[Dict[str, Any]]:
    """
    Fetches data from Google Sheets and returns each row as a dictionary.
    
    Args:
        sheet_name (str): Name of the sheet to fetch
        exclude_columns (list, optional): List of column names to exclude. Defaults to None.

    Returns:
        List[Dict[str, Any]]: List of dictionaries, where each dictionary represents a row
    """
    try:
        values = get_sheet_values(sheet_name)
        if not values or len(values) < 2:
            logging.warning(f"No data found in sheet: {sheet_name}")
            return []

        headers = values[0]
        rows = values[1:]
        
        if exclude_columns is None:
            exclude_columns = []
        
        # Get indices of columns to exclude
        exclude_indices = [i for i, h in enumerate(headers) if h in exclude_columns]
        
        result = []
        for row in rows:
            # Create a dictionary for the row, excluding specified columns
            row_dict = {
                headers[i]: str(cell) if cell is not None else ""
                for i, cell in enumerate(row) 
                if i not in exclude_indices
            }
            result.append(row_dict)
            
        return result
        
    except Exception as e:
        logging.error(f"Error processing sheet {sheet_name}: {str(e)}")
        return []

def row_to_text(row: Dict[str, Any]) -> str:
    """
    Convert a row dictionary to a formatted text string.
    Each key-value pair is on a new line to ensure proper separation.
    """
    return "\n".join([f"{k}: {v}" for k, v in row.items()])

def process_and_store_vectors(rows: List[Dict[str, Any]], 
                            index_name: str, 
                            index_dir: str = "faiss_indices") -> str:
    """
    Process rows into vector embeddings and store using FAISS.
    
    Args:
        rows (List[Dict[str, Any]]): List of row dictionaries
        index_name (str): Name for the FAISS index
        index_dir (str, optional): Directory to store the index. Defaults to "faiss_indices".
        
    Returns:
        str: Path to the saved index
    """
    try:
        # Convert rows to text documents
        texts = [row_to_text(row) for row in rows]
        
        if not texts:
            raise ValueError("No valid text documents to process")
        
        # Initialize the embedding model
        embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
        
        # Create directory if it doesn't exist
        os.makedirs(index_dir, exist_ok=True)
        
        # Create and save FAISS index
        vector_store = FAISS.from_texts(texts, embeddings)
        save_path = os.path.join(index_dir, index_name)
        vector_store.save_local(save_path)
        
        logging.info(f"Successfully created and saved FAISS index to {save_path}")
        return save_path
        
    except Exception as e:
        logging.error(f"Error processing vectors: {str(e)}")
        raise

def process_sheet_to_faiss(sheet_name: str, 
                          index_name: str,
                          exclude_columns: list = None) -> str:
    """
    Complete pipeline: Fetch sheet data and store as FAISS vectors.
    
    Args:
        sheet_name (str): Name of the sheet to process
        index_name (str): Name for the FAISS index
        exclude_columns (list, optional): Columns to exclude. Defaults to None.
        
    Returns:
        str: Path to the saved index
    """
    # 1. Get data from sheet
    rows = get_sheet_as_text(sheet_name, exclude_columns)
    
    if not rows:
        raise ValueError(f"No data found or processed for sheet: {sheet_name}")
    
    # 2. Process and store as FAISS vectors
    return process_and_store_vectors(rows, index_name)


def get_rag_response(query: str, index_path: str) -> str:
    """
    Get a response using RAG (Retrieval-Augmented Generation) for product queries.
    
    Args:
        query (str): User's query about a product
        index_path (str): Path to the FAISS index directory
        
    Returns:
        str: Generated response based on the query and available product information
    """
    try:
        # Initialize the embedding model
        embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
        
        # Load the FAISS index
        vector_store = FAISS.load_local(
            index_path,
            embeddings,
            allow_dangerous_deserialization=True
        )
        
        # Set up the LLM
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash-preview-05-20", temperature=0.4)
        
        # Define the prompt template
        prompt_template = """
You are a consultative sales assistant from NasherMiles. Your goal is to help the customer choose the right luggage based on their needs.

Response Guidelines:
1. If a product is unavailable, clearly mention it.
2. Describe why the product is a good fit for the customer based on its features.
3. Include only ONE shopping link per product or per set.
4. Do not repeat the same shopping link.
5. Avoid AI or bot-like phrasing. Sound natural and professional.
6. Do not greet with "Hi" or "Hey".
7. Keep responses short, clear, and sales-focused.
8. Do not use asterisks (*), markdown formatting, or unnecessary styling.

Use the following context to answer the customer’s query.

Context: {context}

Query: {question}

Answer:
"""

        
        PROMPT = PromptTemplate(
            template=prompt_template,
            input_variables=["context", "question"]
        )
        
        # Create the retrieval chain
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=vector_store.as_retriever(search_kwargs={"k": 30}),
            chain_type_kwargs={"prompt": PROMPT}
        )
        
        # Get the response
        result = qa_chain.invoke({"query": query})
        return result["result"]
        
    except Exception as e:
        print(traceback.format_exc())
        logging.error(f"Error in RAG response generation: {str(e)}")
        return "I'm sorry, I encountered an error while processing your request. Please try again later."


def create_faiss_index_from_data(data: List[Dict[str, Any]], index_path: str) -> None:
    """
    Create a FAISS index from the given data and save it to the specified path.
    
    Args:
        data: List of dictionaries containing the data to index
        index_path: Directory path where to save the FAISS index
    """
    try:
        # Convert data to list of strings
        texts = [row_to_text(row) for row in data]
        
        # Initialize the embedding model
        embeddings = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004")
        
        # Create and save FAISS index
        vector_store = FAISS.from_texts(texts, embeddings)
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(index_path) if os.path.dirname(index_path) else '.', exist_ok=True)
        
        # Save the index
        vector_store.save_local(index_path)
        logging.info(f"Successfully created FAISS index at {index_path}")
        
    except Exception as e:
        logging.error(f"Error creating FAISS index: {str(e)}")
        raise

def query_product_info(query: str, index: str) -> str:
    """
    Query product information using RAG.
    
    Args:
        query (str): User's query about a product
        index (str): Path to the FAISS index directory
        
    Returns:
        str: Generated response
    """
    try:
        # Map index types to their respective paths
        index_path = f"faiss_indices/{index}"
        
        return get_rag_response(query, index_path)
        
    except Exception as e:
        logging.error(f"Error in query_product_info: {str(e)}")
        return "I'm sorry, I encountered an error while processing your request. Please try again later."


if __name__ == "__main__":
    # Example usage
    try:
        # print(get_sheet_as_text("DIY"))
        # process_sheet_to_faiss("DIY", "DIY_index")
        question = "alexandria hardside luggage"
        print(f"Processing question: {question}")
        response = query_product_info(question, "nashermiles")
        print(f"\nResponse: {response}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        import traceback
        traceback.print_exc()

