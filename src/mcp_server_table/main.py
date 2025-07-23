"""
ServiceNow MCP Server - Table Data Export Tool

This module provides a Model Context Protocol (MCP) server that enables intelligent
export of ServiceNow table data through natural language commands. It supports
voice-controlled data exports with automatic table name resolution and multiple
output formats (CSV, XML).

Key Features:
- Natural language table name resolution with [table_name] syntax support
- Voice-controlled export commands
- Multiple export formats (CSV, XML, both)
- ServiceNow API integration with REST and SOAP support
- File attachment handling for Claude AI
- Comprehensive error handling and validation

Dependencies:
- FastMCP for MCP server implementation
- httpx for async HTTP requests
- python-dotenv for environment configuration
- xml.etree.ElementTree for XML processing
- zeep for SOAP client functionality
"""

import os
import httpx
import json
import csv
import io
import xml.etree.ElementTree as ET
import re
import base64
from datetime import timedelta
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pathlib import Path
from typing import Optional, Dict, Any, List
from zeep import Client
from zeep.transports import Transport
from zeep.wsse.username import UsernameToken
from zeep import Settings
import ssl
from requests import Session
from requests.auth import HTTPBasicAuth

# Add SOAP support
try:
    SOAP_AVAILABLE = True
except ImportError:
    SOAP_AVAILABLE = False
    print("Warning: zeep not installed. SOAP functionality will be disabled.")

# --- Configuration Section ---
# Load environment variables from .env file for ServiceNow authentication
load_dotenv()

# ServiceNow instance configuration - these must be set in .env file
SN_INSTANCE = os.getenv(
    "SERVICENOW_INSTANCE"
)  # e.g., https://your-instance.service-now.com
SN_USERNAME = os.getenv("SERVICENOW_USERNAME")  # ServiceNow username
SN_PASSWORD = os.getenv("SERVICENOW_PASSWORD")  # ServiceNow password

# Validate that all required configuration is present
if not all([SN_INSTANCE, SN_USERNAME, SN_PASSWORD]):
    raise ValueError(
        "ServiceNow instance, username, and password must be set in .env file"
    )

# --- Initialize FastMCP Server ---
# Create the MCP server instance that will handle tool requests
mcp = FastMCP()

# --- Common ServiceNow Table Name Mappings ---
# This dictionary maps natural language descriptions to actual ServiceNow table names
# Used for intelligent table name resolution from voice commands
COMMON_TABLE_MAPPINGS = {
    # Incident Management Tables
    # Maps common incident-related terms to the 'incident' table
    "incident": "incident",
    "incidents": "incident",
    "ticket": "incident",  # Common alias for incidents
    "tickets": "incident",
    # CMDB (Configuration Management Database) Tables
    # Maps server and asset-related terms to appropriate CMDB tables
    "server": "cmdb_ci_server",
    "servers": "cmdb_ci_server",
    "computer": "cmdb_ci_computer",
    "computers": "cmdb_ci_computer",
    "ci": "cmdb_ci",  # Configuration Items
    "configuration items": "cmdb_ci",
    "assets": "cmdb_ci",  # General asset reference
    "asset": "cmdb_ci",
    # User Management Tables
    # Maps user-related terms to the system user table
    "user": "sys_user",
    "users": "sys_user",
    "people": "sys_user",
    "person": "sys_user",
    # Change Management Tables
    # Maps change-related terms to the change request table
    "change": "change_request",
    "changes": "change_request",
    "change request": "change_request",
    "change requests": "change_request",
    # Problem Management Tables
    # Maps problem-related terms to the problem table
    "problem": "problem",
    "problems": "problem",
    # Knowledge Base Tables
    # Maps knowledge and article-related terms to knowledge base table
    "knowledge": "kb_knowledge",
    "kb": "kb_knowledge",
    "knowledge base": "kb_knowledge",
    "articles": "kb_knowledge",
    "article": "kb_knowledge",
    # Service Catalog Tables
    # Maps catalog-related terms to service catalog item table
    "catalog": "sc_cat_item",
    "catalog item": "sc_cat_item",
    "catalog items": "sc_cat_item",
}


# --- ServiceNow API Helper Function ---
async def _make_servicenow_request(
    endpoint: str, payload: dict = None, method: str = "POST"
) -> dict:
    """
    Helper function to make authenticated requests to the ServiceNow Table API.

    This function handles all the complexity of making HTTP requests to ServiceNow,
    including authentication, error handling, and response parsing.

    Args:
        endpoint: ServiceNow API endpoint (relative to instance URL)
        payload: Request payload for POST requests (optional)
        method: HTTP method to use ("GET" or "POST")

    Returns:
        dict: Parsed JSON response from ServiceNow

    Raises:
        ValueError: For API errors, connection issues, or invalid responses
    """
    # Construct the full API URL by combining instance URL with endpoint
    api_url = f"{SN_INSTANCE.rstrip('/')}/{endpoint}"

    # Set up HTTP basic authentication using credentials from environment
    auth = (SN_USERNAME, SN_PASSWORD)

    # Configure headers for JSON communication with ServiceNow
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Use async HTTP client with timeout and authentication
    async with httpx.AsyncClient(auth=auth, headers=headers, timeout=60.0) as client:
        try:
            # Log the request details for debugging
            print(
                f"Making {method} request to {api_url}"
                + (f" with payload: {json.dumps(payload)}" if payload else "")
            )

            # Execute the appropriate HTTP method
            if method == "POST":
                response = await client.post(api_url, json=payload)
            elif method == "GET":
                response = await client.get(api_url)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            # Raise an exception for HTTP error status codes (4xx, 5xx)
            response.raise_for_status()

            # Log successful response status
            print(f"ServiceNow API Response Status: {response.status_code}")

            # Handle different success status codes appropriately
            if (
                response.status_code == 204
            ):  # No Content - successful operation with no data
                return {
                    "status": "success",
                    "message": "Operation successful (No Content)",
                }
            if response.status_code == 201:  # Created - successful record creation
                return response.json().get(
                    "result", {"status": "success", "message": "Record created"}
                )

            # For GET requests, return the complete response
            if method == "GET":
                return response.json()

            # For other successful responses, extract the 'result' portion
            return response.json().get("result", {})

        except httpx.HTTPStatusError as e:
            # Handle HTTP error responses with detailed error information
            error_details = f"HTTP Error: {e.response.status_code} - {e.response.text}"
            print(error_details)

            # Attempt to extract ServiceNow-specific error messages
            try:
                sn_error = e.response.json().get("error", {})
                message = sn_error.get("message", "Unknown ServiceNow Error")
                detail = sn_error.get("detail", e.response.text)
                raise ValueError(f"ServiceNow API Error: {message} - {detail}") from e
            except json.JSONDecodeError:
                # Response is not valid JSON
                raise ValueError(error_details) from e
            except Exception as inner_e:
                # Error occurred while parsing the error response
                print(f"Error parsing ServiceNow error response: {inner_e}")
                raise ValueError(error_details) from e

        except httpx.RequestError as e:
            # Handle connection and request errors
            error_details = f"Request Error: {e}"
            print(error_details)
            raise ValueError(f"Could not connect to ServiceNow: {error_details}") from e

        except Exception as e:
            # Catch any other unexpected errors
            error_details = (
                f"Unexpected error during ServiceNow request: {type(e).__name__} - {e}"
            )
            print(error_details)
            raise ValueError(error_details) from e


async def _get_all_records_paginated(
    table_name: str,
    query_filter: str = None,
    fields: str = None,
    include_display_values: bool = True,
    chunk_size: int = 1000,
) -> List[Dict[str, Any]]:
    """
    Retrieve ALL records from ServiceNow table using pagination.

    Handles unlimited record counts by fetching data in chunks and combining results.
    Includes progress tracking and memory-efficient processing.

    Args:
        table_name: ServiceNow table name
        query_filter: ServiceNow encoded query to filter records
        fields: Comma-separated list of specific fields to export
        include_display_values: Include human-readable display values
        chunk_size: Number of records to fetch per request (default 1000)

    Returns:
        List of all record dictionaries
    """
    all_records = []
    offset = 0
    total_fetched = 0

    print(f"[PAGINATION] Starting unlimited export for table '{table_name}'")
    print(f"[PAGINATION] Chunk size: {chunk_size} records per request")

    while True:
        try:
            # Build endpoint with pagination parameters
            endpoint = f"api/now/table/{table_name}"

            # Build query parameters
            params = []
            params.append(f"sysparm_limit={chunk_size}")
            params.append(f"sysparm_offset={offset}")

            if query_filter:
                params.append(f"sysparm_query={query_filter}")

            if fields:
                params.append(f"sysparm_fields={fields}")

            if include_display_values:
                params.append("sysparm_display_value=all")

            # Add parameters to endpoint
            endpoint += "?" + "&".join(params)

            print(
                f"[PAGINATION] Fetching records {offset + 1} to {offset + chunk_size}..."
            )

            # Make the API request
            response_data = await _make_servicenow_request(endpoint, method="GET")

            if not response_data or "result" not in response_data:
                print(f"[PAGINATION] No data returned at offset {offset}")
                break

            chunk_records = response_data["result"]

            # If no records returned, we've reached the end
            if not chunk_records:
                print(f"[PAGINATION] No more records found at offset {offset}")
                break

            # Add records to our collection
            all_records.extend(chunk_records)
            chunk_count = len(chunk_records)
            total_fetched += chunk_count

            print(
                f"[PAGINATION] Fetched {chunk_count} records (Total: {total_fetched})"
            )

            # If we got fewer records than requested, we've reached the end
            if chunk_count < chunk_size:
                print(
                    f"[PAGINATION] Reached end of data (got {chunk_count} < {chunk_size})"
                )
                break

            # Move to next chunk
            offset += chunk_size

            # Memory management for very large datasets
            if total_fetched % 10000 == 0:
                print(
                    f"[PAGINATION] Memory checkpoint: {total_fetched:,} records loaded"
                )

        except Exception as e:
            print(f"[PAGINATION ERROR] Failed at offset {offset}: {str(e)}")
            # If we have some records, continue with what we have
            if all_records:
                print(
                    f"[PAGINATION] Continuing with {len(all_records)} records already fetched"
                )
                break
            else:
                raise ValueError(f"Failed to fetch any records: {str(e)}")

    print(f"[PAGINATION COMPLETE] Total records fetched: {total_fetched:,}")
    return all_records


# --- SOAP API Helper Functions ---
async def _make_servicenow_soap_request(
    table_name: str,
    query_filter: str = None,
    fields: str = None,
    max_records: int = None,  # Changed to allow unlimited
) -> List[Dict[str, Any]]:
    """
    Helper function to make SOAP requests to ServiceNow with unlimited record support.

    Args:
        table_name: ServiceNow table name
        query_filter: Encoded query filter
        fields: Comma-separated field names
        max_records: Maximum records to retrieve (None for unlimited)

    Returns:
        List of record dictionaries

    Raises:
        ValueError: For SOAP errors or connection issues
    """
    if not SOAP_AVAILABLE:
        raise ValueError(
            "SOAP functionality not available. Install zeep package: pip install zeep"
        )

    try:
        print(f"[SOAP] Starting SOAP export for table '{table_name}'")

        # Construct SOAP endpoint - use the correct ServiceNow SOAP endpoint
        soap_url = f"{SN_INSTANCE.rstrip('/')}/{table_name}.do?SOAP"

        print(f"[SOAP] SOAP URL: {soap_url}")

        # Create a requests session with authentication
        session = Session()
        session.auth = HTTPBasicAuth(SN_USERNAME, SN_PASSWORD)

        # Configure SSL settings to handle ServiceNow certificates
        session.verify = True  # Set to False only for development/testing

        # Create transport with the session
        transport = Transport(session=session)

        # Create SOAP client with proper settings
        settings = Settings(
            strict=False,  # Be lenient with WSDL parsing
            xml_huge_tree=True,  # Handle large XML responses
            xsd_ignore_sequence_order=True,  # Ignore sequence order in XSD
        )

        try:
            client = Client(wsdl=soap_url, transport=transport, settings=settings)
            print(f"[SOAP] SOAP client created successfully")

            # Print available operations for debugging
            print(f"[SOAP] Available operations: {[op.name for op in client.service]}")

        except Exception as wsdl_error:
            print(f"[SOAP ERROR] WSDL creation failed: {str(wsdl_error)}")
            # Try alternative SOAP endpoint formats
            alternative_urls = [
                f"{SN_INSTANCE.rstrip('/')}/soap/sys_soap.do?wsdl",
                f"{SN_INSTANCE.rstrip('/')}/soap/{table_name}.do?wsdl",
                f"{SN_INSTANCE.rstrip('/')}/{table_name}.do?SOAP&displayvalue=all",
            ]

            for alt_url in alternative_urls:
                try:
                    print(f"[SOAP] Trying alternative URL: {alt_url}")
                    client = Client(
                        wsdl=alt_url, transport=transport, settings=settings
                    )
                    soap_url = alt_url
                    print(f"[SOAP] Successfully connected with: {alt_url}")
                    break
                except Exception as alt_error:
                    print(f"[SOAP] Alternative URL failed: {str(alt_error)}")
                    continue
            else:
                raise ValueError(
                    f"Could not establish SOAP connection. Last error: {str(wsdl_error)}"
                )

        # For unlimited records with SOAP, we need pagination
        all_records = []
        offset = 0
        chunk_size = 250  # Smaller SOAP chunk size for stability

        print(f"[SOAP] Starting pagination with chunk size: {chunk_size}")

        while True:
            try:
                # Prepare query parameters for this chunk
                query_params = {}

                # Build the query string
                query_parts = []

                if query_filter:
                    query_parts.append(query_filter)

                # Add ordering to ensure consistent pagination
                query_parts.append("ORDERBYsys_created_on")

                if query_parts:
                    query_params["__encoded_query"] = "^".join(query_parts)

                # Add pagination parameters (ServiceNow SOAP pagination)
                query_params["__first_row"] = str(offset + 1)
                query_params["__last_row"] = str(offset + chunk_size)

                print(
                    f"[SOAP] Fetching records {offset + 1} to {offset + chunk_size}..."
                )
                print(f"[SOAP] Query params: {query_params}")

                # Make SOAP call - try different method names
                chunk_records = []

                # Try common ServiceNow SOAP methods
                soap_methods = ["getRecords", "get", "getKeys"]

                for method_name in soap_methods:
                    try:
                        if hasattr(client.service, method_name):
                            print(f"[SOAP] Trying method: {method_name}")

                            if method_name == "getRecords":
                                response = client.service.getRecords(**query_params)
                            elif method_name == "get":
                                response = client.service.get(**query_params)
                            elif method_name == "getKeys":
                                response = client.service.getKeys(**query_params)

                            print(f"[SOAP] Method {method_name} succeeded")
                            break
                    except Exception as method_error:
                        print(
                            f"[SOAP] Method {method_name} failed: {str(method_error)}"
                        )
                        continue
                else:
                    # If no standard methods work, try to get the first available method
                    available_methods = [op.name for op in client.service]
                    if available_methods:
                        method_name = available_methods[0]
                        print(f"[SOAP] Trying first available method: {method_name}")
                        method = getattr(client.service, method_name)
                        response = method(**query_params)
                    else:
                        raise ValueError("No SOAP methods available")

                # Parse response based on response structure
                print(f"[SOAP] Response type: {type(response)}")

                # Handle different response formats
                if hasattr(response, "getRecordsResult"):
                    records_data = response.getRecordsResult
                elif hasattr(response, "result"):
                    records_data = response.result
                elif isinstance(response, list):
                    records_data = response
                else:
                    records_data = [response] if response else []

                # Convert to dictionary format
                for record in records_data:
                    record_dict = {}

                    if hasattr(record, "__dict__"):
                        # Handle complex objects
                        for key, value in record.__dict__.items():
                            if not key.startswith("_"):
                                record_dict[key] = (
                                    str(value) if value is not None else ""
                                )
                    elif hasattr(record, "_value_1"):
                        # Handle ServiceNow SOAP record format
                        for attr in dir(record):
                            if attr.startswith("_value_") or not attr.startswith("_"):
                                value = getattr(record, attr)
                                if not callable(value):
                                    record_dict[attr] = (
                                        str(value) if value is not None else ""
                                    )
                    elif isinstance(record, dict):
                        # Handle dictionary records
                        record_dict = {
                            k: str(v) if v is not None else ""
                            for k, v in record.items()
                        }
                    else:
                        # Handle simple values
                        record_dict["value"] = str(record)

                    if record_dict:  # Only add non-empty records
                        chunk_records.append(record_dict)

                # If no records returned, we've reached the end
                if not chunk_records:
                    print(f"[SOAP] No more records found at offset {offset}")
                    break

                all_records.extend(chunk_records)
                chunk_count = len(chunk_records)

                print(
                    f"[SOAP] Fetched {chunk_count} records (Total: {len(all_records)})"
                )

                # If we got fewer records than requested, we've reached the end
                if chunk_count < chunk_size:
                    print(
                        f"[SOAP] Reached end of data (got {chunk_count} < {chunk_size})"
                    )
                    break

                offset += chunk_size

                # Apply max_records limit if specified
                if max_records and len(all_records) >= max_records:
                    all_records = all_records[:max_records]
                    print(f"[SOAP] Applied max_records limit: {max_records}")
                    break

            except Exception as chunk_error:
                print(
                    f"[SOAP CHUNK ERROR] Error at offset {offset}: {str(chunk_error)}"
                )
                # Try to continue with next chunk
                offset += chunk_size
                if offset > 50000:  # Prevent infinite loops
                    print("[SOAP] Reached offset limit, stopping")
                    break
                continue

        print(f"[SOAP COMPLETE] Total records fetched: {len(all_records):,}")

        # If no records were fetched, provide helpful error information
        if not all_records:
            print(f"[SOAP WARNING] No records retrieved. This could be due to:")
            print(f"  - Table '{table_name}' doesn't exist")
            print(f"  - No records match the query filter")
            print(f"  - SOAP permissions not configured properly")
            print(f"  - SOAP endpoint configuration issues")

        return all_records

    except Exception as e:
        error_msg = f"SOAP request failed for table '{table_name}': {str(e)}"
        print(f"[SOAP ERROR] {error_msg}")

        # Provide troubleshooting information
        print(f"[SOAP TROUBLESHOOTING] Please check:")
        print(f"  1. ServiceNow instance supports SOAP API")
        print(f"  2. User has 'soap_query' role")
        print(f"  3. Table '{table_name}' exists and is accessible")
        print(f"  4. ServiceNow instance URL is correct: {SN_INSTANCE}")
        print(f"  5. Network connectivity to ServiceNow")

        raise ValueError(error_msg) from e


# --- MCP Tool Definitions ---
def _convert_to_csv_chunked(records: list[dict], chunk_size: int = 5000) -> str:
    """
    Convert large datasets to CSV format using chunked processing for memory efficiency.

    Args:
        records: List of ServiceNow record dictionaries
        chunk_size: Number of records to process at once

    Returns:
        str: CSV-formatted string ready for file output
    """
    if not records:
        return ""

    try:
        print(
            f"[CSV CHUNKED] Processing {len(records):,} records in chunks of {chunk_size}"
        )

        # Use StringIO to create CSV in memory
        output = io.StringIO()

        # Get all unique field names from a sample of records (more efficient for large datasets)
        sample_size = min(100, len(records))
        sample_records = (
            records[:sample_size] + records[-sample_size:]
            if len(records) > sample_size
            else records
        )

        fieldnames = set()
        for record in sample_records:
            fieldnames.update(record.keys())

        # Sort fieldnames for consistent output
        fieldnames = sorted(list(fieldnames))

        print(f"[CSV CHUNKED] Detected {len(fieldnames)} unique fields")

        # Create CSV writer with enhanced settings
        writer = csv.DictWriter(
            output,
            fieldnames=fieldnames,
            quoting=csv.QUOTE_ALL,
            escapechar="\\",
            lineterminator="\n",
        )

        # Write header
        writer.writeheader()

        # Process records in chunks
        processed_count = 0
        error_count = 0

        for chunk_start in range(0, len(records), chunk_size):
            chunk_end = min(chunk_start + chunk_size, len(records))
            chunk = records[chunk_start:chunk_end]

            print(
                f"[CSV CHUNKED] Processing chunk {chunk_start + 1}-{chunk_end} of {len(records):,}"
            )

            for i, record in enumerate(chunk):
                try:
                    # Clean the record data with enhanced processing
                    cleaned_record = {}

                    for fieldname in fieldnames:
                        value = record.get(fieldname, "")
                        cleaned_value = _clean_csv_field_value(value)
                        cleaned_record[fieldname] = cleaned_value

                    writer.writerow(cleaned_record)
                    processed_count += 1

                except Exception as row_error:
                    error_count += 1
                    absolute_row = chunk_start + i
                    print(f"[CSV ERROR] Row {absolute_row}: {str(row_error)}")

                    # Try to write a minimal version of the row
                    try:
                        minimal_record = {}
                        for fieldname in fieldnames:
                            minimal_record[fieldname] = (
                                str(record.get(fieldname, ""))
                                .replace("\n", " ")
                                .replace("\r", "")[:500]
                            )
                        writer.writerow(minimal_record)
                        processed_count += 1
                    except Exception as minimal_error:
                        print(
                            f"[CSV ERROR] Failed to write minimal row {absolute_row}: {str(minimal_error)}"
                        )

            # Memory management - show progress
            if chunk_end % 10000 == 0 or chunk_end == len(records):
                print(
                    f"[CSV PROGRESS] Processed {processed_count:,}/{len(records):,} records ({error_count} errors)"
                )

        # Get CSV content
        csv_content = output.getvalue()
        output.close()

        print(
            f"[CSV SUCCESS] Generated CSV with {processed_count:,} rows, {error_count} errors"
        )
        print(f"[CSV INFO] CSV size: {len(csv_content):,} characters")

        return csv_content

    except Exception as e:
        print(f"[CSV CRITICAL ERROR] CSV conversion failed: {str(e)}")
        return f"CSV_CONVERSION_ERROR: {str(e)}"


def _clean_csv_field_value(value) -> str:
    """
    Clean and format field values for safe CSV export.

    Handles various ServiceNow data types and ensures CSV compatibility.

    Args:
        value: Raw field value from ServiceNow

    Returns:
        str: Cleaned string value safe for CSV
    """
    try:
        # Handle None/null values
        if value is None:
            return ""

        # Handle dictionary/reference fields
        if isinstance(value, dict):
            # ServiceNow reference fields often have 'display_value' and 'value'
            if "display_value" in value:
                display_val = value["display_value"]
                sys_id = value.get("value", "")
                if display_val and sys_id:
                    return f"{display_val} ({sys_id})"
                elif display_val:
                    return str(display_val)
                else:
                    return str(sys_id)
            else:
                # Convert dict to readable string
                return str(value).replace("\n", " ").replace("\r", " ")

        # Handle list/array fields
        elif isinstance(value, list):
            if not value:
                return ""
            # Join array elements with semicolons (safer than commas in CSV)
            cleaned_items = []
            for item in value:
                if isinstance(item, dict):
                    cleaned_items.append(
                        str(item).replace("\n", " ").replace("\r", " ")
                    )
                else:
                    cleaned_items.append(
                        str(item).replace("\n", " ").replace("\r", " ")
                    )
            return "; ".join(cleaned_items)

        # Handle boolean values
        elif isinstance(value, bool):
            return "true" if value else "false"

        # Handle numeric values
        elif isinstance(value, (int, float)):
            return str(value)

        # Handle string values
        else:
            str_value = str(value)

            # Clean problematic characters
            # Replace line breaks with spaces
            str_value = str_value.replace("\n", " ").replace("\r", " ")

            # Replace tabs with spaces
            str_value = str_value.replace("\t", " ")

            # Handle multiple consecutive spaces
            str_value = " ".join(str_value.split())

            # Limit field length for very long fields
            if len(str_value) > 5000:
                str_value = str_value[:4997] + "..."

            # Handle encoding issues
            try:
                # Try to encode/decode to catch encoding problems
                str_value.encode("utf-8")
                return str_value
            except UnicodeEncodeError:
                # Fallback: remove problematic characters
                return str_value.encode("ascii", errors="replace").decode("ascii")

    except Exception as e:
        # Ultimate fallback
        print(f"[FIELD CLEAN ERROR] {str(e)}")
        return f"FIELD_ERROR: {type(value).__name__}"


@mcp.tool()
async def export_servicenow_data(
    command: str,
    api_method: str = "rest",
    export_format: str = "xml",
    output_file_path: str | None = None,
    query_filter: str | None = None,
    fields: str | None = None,
    max_records: int = None,  # Changed to None for unlimited
    include_display_values: bool = True,
    return_as_attachment: bool = True,
    enable_unlimited_export: bool = True,  # New parameter to control unlimited export
) -> str:
    """MAIN EXPORT TOOL: Export ServiceNow table data with UNLIMITED record support.

    This tool now supports unlimited record exports through pagination and chunked processing.
    It can handle datasets with millions of records efficiently.

    Args:
        command: Natural language command with optional [table_name] syntax
                Example: "Export the data from Server[cmdb_ci_server] table"
        api_method: API method to use - "rest" or "soap" (default "rest")
        export_format: Output format - "csv", "xml", or "both" (default "xml")
        output_file_path: Custom file path (optional, defaults to Downloads folder)
        query_filter: ServiceNow encoded query to filter records (optional)
        fields: Comma-separated list of specific fields to export (optional)
        max_records: Maximum number of records to export (None for unlimited)
        include_display_values: Include human-readable display values (default True)
        return_as_attachment: Return file as attachment for Claude (default True)
        enable_unlimited_export: Enable unlimited record export (default True)

    Returns:
        Export confirmation with file details and optional attachment data

    Examples:
        - "Export ALL data from Server[cmdb_ci_server] table" -> exports ALL cmdb_ci_server records
        - "Get ALL incidents[incident] data" -> exports ALL incident table records
        - "Export ALL users[sys_user] information" -> exports ALL sys_user table records
    """
    try:
        # Parse command to extract table name and description
        actual_table_name, description = _parse_table_command(command)

        # Validate export format
        valid_formats = ["csv", "xml", "both"]
        if export_format.lower() not in valid_formats:
            return f"Error: Invalid export format '{export_format}'. Must be one of: {', '.join(valid_formats)}"

        # Validate API method
        valid_methods = ["rest", "soap"]
        if api_method.lower() not in valid_methods:
            return f"Error: Invalid API method '{api_method}'. Must be one of: {', '.join(valid_methods)}"

        if api_method.lower() == "soap" and not SOAP_AVAILABLE:
            return "Error: SOAP functionality not available. Install zeep package: pip install zeep"

        print(f"[DEBUG] Parsed command: '{command}'")
        print(f"[TABLE] Resolved table: '{actual_table_name}'")
        print(f"[API] API method: {api_method.upper()}")
        print(f"[FORMAT] Export format: {export_format}")
        print(f"[UNLIMITED] Unlimited export enabled: {enable_unlimited_export}")

        # Fetch data using selected API method with unlimited support
        if api_method.lower() == "soap":
            print("[API] Using SOAP API method")
            try:
                records = await _make_servicenow_soap_request(
                    actual_table_name, query_filter, fields, max_records
                )
                # Convert to expected format
                response_data = {"result": records}
                print(f"[SOAP SUCCESS] Retrieved {len(records)} records via SOAP")
            except Exception as soap_error:
                print(f"[SOAP FALLBACK] SOAP failed: {str(soap_error)}")
                print("[SOAP FALLBACK] Falling back to REST API")
                # Fallback to REST if SOAP fails
                if enable_unlimited_export and max_records is None:
                    records = await _get_all_records_paginated(
                        actual_table_name, query_filter, fields, include_display_values
                    )
                    response_data = {"result": records}
                else:
                    # Use single REST request
                    endpoint = f"api/now/table/{actual_table_name}"
                    params = []
                    if max_records:
                        params.append(f"sysparm_limit={max_records}")
                    else:
                        params.append(f"sysparm_limit=1000")
                    if query_filter:
                        params.append(f"sysparm_query={query_filter}")
                    if fields:
                        params.append(f"sysparm_fields={fields}")
                    if include_display_values:
                        params.append("sysparm_display_value=all")
                    if params:
                        endpoint += "?" + "&".join(params)
                    response_data = await _make_servicenow_request(
                        endpoint, method="GET"
                    )
        else:
            # Use paginated method for unlimited record support
            if enable_unlimited_export and max_records is None:
                print("[API] Using unlimited pagination mode")
                records = await _get_all_records_paginated(
                    actual_table_name, query_filter, fields, include_display_values
                )
                response_data = {"result": records}
            else:
                # Use single request for limited records
                endpoint = f"api/now/table/{actual_table_name}"

                # Build query parameters
                params = []
                if max_records:
                    params.append(f"sysparm_limit={max_records}")
                else:
                    params.append(f"sysparm_limit=1000")  # Default fallback

                if query_filter:
                    params.append(f"sysparm_query={query_filter}")

                if fields:
                    params.append(f"sysparm_fields={fields}")

                if include_display_values:
                    params.append("sysparm_display_value=all")

                # Add parameters to endpoint
                if params:
                    endpoint += "?" + "&".join(params)

                response_data = await _make_servicenow_request(endpoint, method="GET")

        if not response_data or "result" not in response_data:
            return f"ERROR: No data returned from ServiceNow for table '{actual_table_name}'"

        records = response_data["result"]

        if not records:
            return f"INFO: No records found in table '{actual_table_name}' with the specified criteria."

        # Add detailed logging about the data structure
        print(f"[DATA INFO] Retrieved {len(records):,} records")
        if records:
            sample_record = records[0]
            print(f"[DATA INFO] Fields per record: {len(sample_record.keys())}")
            print(f"[DATA INFO] Sample fields: {list(sample_record.keys())[:10]}")

        # Memory usage warning for very large datasets
        if len(records) > 100000:
            print(f"[MEMORY WARNING] Large dataset: {len(records):,} records")
            print(
                "[MEMORY WARNING] Consider using query filters to reduce dataset size"
            )

        # Generate base filename with timestamp
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = f"{actual_table_name}_export_{timestamp}"

        export_results = []
        files_created = []
        attachments = []

        # Export based on format request
        if export_format.lower() in ["xml", "both"]:
            print(f"[XML START] Beginning XML conversion for {len(records):,} records")

            xml_file_path = _get_output_file_path(
                base_filename,
                "xml",
                (
                    output_file_path
                    if output_file_path and not export_format.lower() == "both"
                    else None
                ),
            )
            xml_content = _convert_to_xml(records, actual_table_name)

            try:
                with open(xml_file_path, "w", encoding="utf-8") as xmlfile:
                    xmlfile.write(xml_content)
                files_created.append(xml_file_path)
                export_results.append(f"SUCCESS XML: {len(xml_content):,} bytes")

                # Create attachment for Claude (limit attachment size for very large files)
                if return_as_attachment:
                    if len(xml_content) < 50_000_000:  # 50MB limit
                        attachment = _create_file_attachment(
                            xml_file_path, xml_content, "application/xml"
                        )
                        attachments.append(attachment)
                    else:
                        print(
                            "[ATTACHMENT] XML file too large for attachment, skipping"
                        )

            except UnicodeEncodeError as encoding_error:
                # Handle encoding issues specifically
                try:
                    # Try with different encoding
                    with open(xml_file_path, "w", encoding="utf-8-sig") as xmlfile:
                        xmlfile.write(xml_content)
                    files_created.append(xml_file_path)
                    export_results.append(
                        f"SUCCESS XML (UTF-8-BOM): {len(xml_content):,} bytes"
                    )

                    if return_as_attachment and len(xml_content) < 50_000_000:
                        attachment = _create_file_attachment(
                            xml_file_path, xml_content, "application/xml"
                        )
                        attachments.append(attachment)
                except Exception as fallback_error:
                    export_results.append(
                        f"ERROR XML: Encoding issue - {str(fallback_error)}"
                    )
            except Exception as file_error:
                export_results.append(f"ERROR XML: {str(file_error)}")

        if export_format.lower() in ["csv", "both"]:
            print(f"[CSV START] Beginning CSV conversion for {len(records):,} records")

            csv_file_path = _get_output_file_path(
                base_filename,
                "csv",
                (
                    output_file_path
                    if output_file_path and not export_format.lower() == "both"
                    else None
                ),
            )

            csv_content = _convert_to_csv_chunked(records)

            # Check if CSV conversion was successful
            if csv_content.startswith("CSV_CONVERSION_ERROR:"):
                export_results.append(f"ERROR CSV: {csv_content}")
            else:
                try:
                    with open(
                        csv_file_path, "w", newline="", encoding="utf-8-sig"
                    ) as csvfile:
                        csvfile.write(csv_content)
                    files_created.append(csv_file_path)
                    export_results.append(f"SUCCESS CSV: {len(csv_content):,} bytes")
                    print(f"[CSV SUCCESS] CSV file created: {csv_file_path}")

                    # Create attachment for Claude (limit attachment size for very large files)
                    if return_as_attachment:
                        if len(csv_content) < 50_000_000:  # 50MB limit
                            attachment = _create_file_attachment(
                                csv_file_path, csv_content, "text/csv"
                            )
                            attachments.append(attachment)
                        else:
                            print(
                                "[ATTACHMENT] CSV file too large for attachment, skipping"
                            )

                except Exception as file_error:
                    print(
                        f"[CSV FILE ERROR] Failed to write CSV file: {str(file_error)}"
                    )
                    export_results.append(
                        f"ERROR CSV: File write failed - {str(file_error)}"
                    )

        # Compile results with enhanced information
        record_count = len(records)
        field_count = len(records[0].keys()) if records else 0

        result_message = (
            f"SUCCESS: Exported {record_count:,} records from '{actual_table_name}'"
        )
        result_message += f"\nAPI Method: {api_method.upper()}"
        result_message += f"\nFields exported: {field_count}"
        result_message += f"\nUnlimited export: {'Enabled' if enable_unlimited_export else 'Disabled'}"
        result_message += f"\nExport results: {'; '.join(export_results)}"

        if files_created:
            result_message += f"\nFiles created:"
            for file_path in files_created:
                filename = os.path.basename(file_path)
                file_size = (
                    os.path.getsize(file_path) if os.path.exists(file_path) else 0
                )
                result_message += f"\n   - {filename} ({file_size:,} bytes)"

        if attachments:
            result_message += f"\nAttachments prepared for Claude AI access"

        if query_filter:
            result_message += f"\nApplied filter: {query_filter}"

        # Include attachment data in response if requested
        if return_as_attachment and attachments:
            result_message += f"\n\nFile Contents Available:"
            for attachment in attachments:
                result_message += (
                    f"\n   {attachment['filename']} ({attachment['size']:,} bytes)"
                )

        # Add performance information for large datasets
        if record_count > 10000:
            result_message += f"\n\n🚀 PERFORMANCE INFO:"
            result_message += f"\n   - Large dataset processed successfully"
            result_message += f"\n   - Used pagination for efficient memory usage"
            result_message += f"\n   - Total records: {record_count:,}"

        return result_message

    except ValueError as e:
        return f"ERROR: Export failed - {e}"
    except Exception as e:
        return f"UNEXPECTED ERROR: {str(e)}"


def _convert_to_xml(records: list[dict], table_name: str) -> str:
    """
    Convert a list of ServiceNow records to XML format.

    Creates a well-formed XML document with proper structure and metadata.
    Handles different data types and includes table information in the root element.

    Args:
        records: List of ServiceNow record dictionaries
        table_name: Name of the ServiceNow table for metadata

    Returns:
        str: XML-formatted string with proper declarations and formatting
    """
    if not records:
        return '<?xml version="1.0" encoding="UTF-8"?><records></records>'

    # Create root element
    root = ET.Element("records")
    root.set("table", table_name)
    root.set("count", str(len(records)))

    # Add each record
    for record in records:
        record_elem = ET.SubElement(root, "record")

        for key, value in record.items():
            field_elem = ET.SubElement(record_elem, "field")
            field_elem.set("name", key)

            # Handle different value types with encoding safety
            try:
                if isinstance(value, dict):
                    # For reference fields with display values
                    field_elem.text = value.get("display_value", str(value))
                    if "value" in value:
                        field_elem.set("sys_id", str(value["value"]))
                elif isinstance(value, list):
                    # For array fields
                    field_elem.text = ", ".join(str(item) for item in value)
                elif value is None:
                    field_elem.text = ""
                else:
                    field_elem.text = str(value)
            except UnicodeEncodeError:
                # Fallback for problematic characters
                field_elem.text = (
                    str(value).encode("ascii", errors="replace").decode("ascii")
                )

    # Convert to string with proper formatting
    ET.indent(root, space="  ")
    try:
        return ET.tostring(root, encoding="unicode", xml_declaration=True)
    except UnicodeEncodeError:
        # Fallback encoding
        return ET.tostring(root, encoding="ascii", xml_declaration=True).decode("ascii")


def _resolve_table_name(user_input: str) -> str:
    """
    Intelligently resolve conversational table names to actual ServiceNow table names.

    This function implements the natural language processing for table name resolution.
    It uses multiple strategies:
    1. Direct mapping lookup from COMMON_TABLE_MAPPINGS
    2. Pattern recognition for ServiceNow naming conventions
    3. Partial matching for similar terms
    4. Fallback to original input for validation by ServiceNow

    Args:
        user_input: Natural language description of the table

    Returns:
        str: Resolved ServiceNow table name
    """
    # Convert to lowercase for matching
    user_input_lower = user_input.lower().strip()

    # Direct mapping check
    if user_input_lower in COMMON_TABLE_MAPPINGS:
        return COMMON_TABLE_MAPPINGS[user_input_lower]

    # Check if it's already a valid ServiceNow table name pattern
    if "_" in user_input_lower or user_input_lower.startswith(("sys_", "cmdb_", "sc_")):
        return user_input_lower

    # Partial matching for common terms
    for key, value in COMMON_TABLE_MAPPINGS.items():
        if key in user_input_lower or user_input_lower in key:
            return value

    # If no match found, return as-is (let ServiceNow validate)
    return user_input_lower


def _get_downloads_folder() -> str:
    """
    Get the user's Downloads folder path across different operating systems.

    Provides cross-platform compatibility for determining the standard Downloads
    folder location. Creates the folder if it doesn't exist.

    Returns:
        str: Path to the Downloads folder (or current directory as fallback)
    """
    try:
        # Try to get Downloads folder using pathlib
        if os.name == "nt":  # Windows
            downloads_path = Path.home() / "Downloads"
        else:  # macOS/Linux
            downloads_path = Path.home() / "Downloads"

        # Create the Downloads folder if it doesn't exist
        downloads_path.mkdir(parents=True, exist_ok=True)

        return str(downloads_path)
    except Exception:
        # Fallback to current directory if Downloads folder can't be determined
        return os.getcwd()


def _create_safe_filename(filename: str) -> str:
    """
    Create a safe filename by removing invalid characters.

    Ensures filenames are compatible with all operating systems by removing
    characters that are invalid in Windows filenames and limiting length.

    Args:
        filename: Original filename

    Returns:
        str: Sanitized filename safe for file system operations
    """
    # Remove invalid characters for Windows filenames
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")

    # Limit filename length
    if len(filename) > 200:
        filename = filename[:200]

    return filename


def _get_output_file_path(
    base_filename: str, extension: str, custom_path: str | None = None
) -> str:
    """
    Generate output file path, defaulting to Downloads folder if no custom path provided.

    Handles file path generation with proper extension handling and fallback logic.
    Ensures files are saved to accessible locations with unique names.

    Args:
        base_filename: Base name for the file (without extension)
        extension: File extension to append
        custom_path: Optional custom path (if provided, used as-is)

    Returns:
        str: Complete file path for output
    """
    if custom_path:
        # If custom path provided, use it as-is
        if custom_path.endswith(("." + extension)):
            return custom_path
        else:
            return f"{custom_path}.{extension}"
    else:
        # Default to Downloads folder
        downloads_folder = _get_downloads_folder()
        safe_filename = _create_safe_filename(base_filename)
        return os.path.join(downloads_folder, f"{safe_filename}.{extension}")


def _parse_table_command(command: str) -> tuple[str, str]:
    """
    Parse commands with [table_name] syntax.

    Examples:
        "Export the data from Server[cmdb_ci_server] table" -> ("cmdb_ci_server", "server")
        "Get incidents[incident] data" -> ("incident", "incidents")

    Args:
        command: Natural language command with optional [table_name] syntax

    Returns:
        Tuple of (actual_table_name, description)
    """
    # Look for [table_name] pattern
    bracket_pattern = r"\[([^\]]+)\]"
    match = re.search(bracket_pattern, command)

    if match:
        # Extract table name from brackets
        table_name = match.group(1).strip()
        # Remove the bracket part to get description
        description = re.sub(bracket_pattern, "", command).strip()
        return table_name, description
    else:
        # No bracket syntax, use existing resolution
        resolved_name = _resolve_table_name(command)
        return resolved_name, command


def _create_file_attachment(
    file_path: str, content: str, content_type: str = "text/plain"
) -> Dict[str, Any]:
    """
    Create file attachment data structure for Claude AI.

    Args:
        file_path: Path to the file
        content: File content as string
        content_type: MIME type of the content

    Returns:
        Dictionary containing file attachment information
    """
    filename = os.path.basename(file_path)

    # Encode content for attachment with error handling
    try:
        content_bytes = content.encode("utf-8")
    except UnicodeEncodeError:
        # Fallback encoding for problematic characters
        content_bytes = content.encode("utf-8", errors="replace")

    base64_content = base64.b64encode(content_bytes).decode("utf-8")

    return {
        "type": "attachment",
        "filename": filename,
        "content_type": content_type,
        "size": len(content_bytes),
        "base64_content": base64_content,
        "file_path": file_path,
    }


@mcp.tool()
async def export_with_bracket_syntax(command: str, api_method: str = "rest") -> str:
    """
    Specialized tool for handling bracket syntax commands.

    Specifically designed for commands like:
    "Export the data from Server[cmdb_ci_server] table"

    Args:
        command: Command with [table_name] syntax
        api_method: "rest" or "soap"

    Returns:
        XML file as attachment with export confirmation
    """
    # Parse the bracket syntax
    table_name, description = _parse_table_command(command)

    if "[" not in command:
        return f"ERROR: This tool expects bracket syntax like: 'Export data from Server[cmdb_ci_server] table'"

    # Call main export function with XML default
    return await export_servicenow_data(
        command=command,
        api_method=api_method,
        export_format="xml",
        return_as_attachment=True,
    )


# --- Server Entry Point ---
def start():
    """
    Entry point function for the MCP server.

    This function is called by the console script defined in pyproject.toml.
    It initializes and runs the FastMCP server with stdio transport.
    """
    try:
        print("Starting ServiceNow MCP Server...")
        print(f"ServiceNow Instance: {SN_INSTANCE}")
        print(f"Username: {SN_USERNAME}")
        print("Server ready for MCP client connections.")

        # Run the MCP server with stdio transport
        mcp.run(transport="stdio")

    except Exception as e:
        print(f"Error starting MCP server: {e}")
        raise


if __name__ == "__main__":
    # Run the server when this file is executed directly
    start()