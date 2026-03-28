"""
Simple Firecrawl Web Scraper - Extract Structured Data from Pages
"""

import os
from dotenv import load_dotenv
from firecrawl import FirecrawlApp

# Load environment variables from .env file
load_dotenv()

def scrape_page(url, schema=None):
    """
    Extract structured data from a webpage.

    Args:
        url (str): The website URL to scrape
        schema (dict): Optional schema to structure the extracted data

    Returns:
        dict: Extracted data from the page
    """

    # Initialize Firecrawl with your API key
    api_key = os.getenv('FIRECRAWL_API_KEY')

    if not api_key:
        print("❌ Error: FIRECRAWL_API_KEY not found in .env file")
        return None

    app = FirecrawlApp(api_key=api_key)

    print(f"🔍 Scraping: {url}")

    # Scrape the page
    result = app.scrape_url(url)

    if result:
        print("✅ Successfully scraped the page!")
        return result
    else:
        print("❌ Failed to scrape the page")
        return None


def scrape_with_schema(url, schema):
    """
    Extract structured data using a specific schema.
    This is more powerful - you define exactly what data you want.

    Args:
        url (str): The website URL to scrape
        schema (dict): Schema defining what to extract

    Returns:
        dict: Structured data matching your schema
    """

    api_key = os.getenv('FIRECRAWL_API_KEY')

    if not api_key:
        print("❌ Error: FIRECRAWL_API_KEY not found in .env file")
        return None

    app = FirecrawlApp(api_key=api_key)

    print(f"🔍 Scraping with schema: {url}")

    # Scrape with a schema for structured data
    result = app.scrape_url(url, {
        'formats': ['extract'],
        'extract': {
            'schema': schema
        }
    })

    if result:
        print("✅ Successfully extracted structured data!")
        return result
    else:
        print("❌ Failed to extract data")
        return None


if __name__ == "__main__":
    # Example 1: Simple page scrape
    print("=" * 50)
    print("EXAMPLE 1: Simple Page Scrape")
    print("=" * 50)

    result = scrape_page("https://example.com")
    if result:
        print("\nExtracted Data:")
        print(result)


    # Example 2: Scrape with a custom schema
    print("\n" + "=" * 50)
    print("EXAMPLE 2: Scrape with Schema (Blog Post)")
    print("=" * 50)

    # Define what data you want to extract
    blog_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "The main title of the blog post"
            },
            "author": {
                "type": "string",
                "description": "The author of the blog post"
            },
            "date": {
                "type": "string",
                "description": "Publication date"
            },
            "content": {
                "type": "string",
                "description": "The main content of the blog post"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags or categories for the post"
            }
        }
    }

    # Try with a real blog URL
    blog_result = scrape_with_schema("https://www.example.com/blog-post", blog_schema)
    if blog_result:
        print("\nExtracted Blog Data:")
        print(blog_result)
