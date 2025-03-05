from azure.identity import ClientAssertionCredential
from azure.storage.blob import BlobServiceClient
import os
import psycopg2
import pandas as pd
from PIL import Image
import pandas as pd
import uuid
import io
import datetime
import requests
import threading
import queue

storage_account_key = "o2zPMzmBOPLX5fOU+4qfZHM3nTJ/PebVpDwpje9yeYvQRukEEKW2bXicwq0IJyseNjMDNVPu2fTP+AStzhO6ew=="
storage_account_name = "filesabsv1"
connection_string = "DefaultEndpointsProtocol=https;AccountName=filesabsv1;AccountKey=o2zPMzmBOPLX5fOU+4qfZHM3nTJ/PebVpDwpje9yeYvQRukEEKW2bXicwq0IJyseNjMDNVPu2fTP+AStzhO6ew==;EndpointSuffix=core.windows.net"
container_name = "originalimages"
container_name_compressed = "compressesimages"

def get_db_connection():
    return psycopg2.connect(user='postgres', password="password", host="localhost", database='spyne')


def upload_blob_storage(file_path, blob_name):
    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        
        with open(file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True)
        
        print(f"Uploaded {blob_name} successfully")
        return blob_client.url
    except Exception as e:
        print(f"Error uploading {blob_name}: {e}")
        return None


def db_execute(query, params, retval=False, bulk=False, single=None):
    record = None
    res = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if bulk:
            cursor.executemany(query, params)
        else:
            cursor.execute(query, params)
        conn.commit()
        if retval:
            record = cursor.fetchone() if single else cursor.fetchall()
    except Exception as e:
        conn.rollback()
        print(f"Database query failed: {e}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return record
    
def db_execute_query(query,params, retrival = None, isbulk=None):
    conn = get_db_connection()
    cur = conn.cursor()
    res = None
    try:
        if isbulk:
            cur.executemany(query,params)
        else:
            cur.execute(query,params)
        conn.commit()
        if retrival:
            res = True
    except Exception as e:
        conn.rollback() 
        print(f"Error during bulk insert: {e}")
    finally:
        cur.close()
        conn.close() 
    return res


result_queue = queue.Queue()

def process_csv(df,request_id):
    query = '''
    INSERT INTO requests (request_id, status, created_at)
    VALUES (%s, %s, %s)
    '''
    params = (str(request_id),'pending', datetime.datetime.now())
    dbres = db_execute_query(query=query,params=params,retrival=True)
    if not dbres:
        return "Database not updated"
    threads = []
    try:
        for index, row in df.iterrows():
            product_name = row['Product Name']
            image_urls = row['Input Image Urls'].split(',')

            for url in image_urls:
                url = url.strip()
                thread = threading.Thread(target=process_image, args=(url, request_id, product_name, result_queue))
                threads.append(thread)
                thread.start()
        for thread in threads:
            thread.join()

        results = []
        while not result_queue.empty():
            result = result_queue.get()
            product_name = result[0]
            original_url = result[1]
            compressed_url = result[3]
            request_id = request_id
            status = 'completed'
            results.append((str(request_id), product_name, original_url, compressed_url, status))
        if results:
            query = "INSERT INTO image (request_id, product_name, original_url, compressed_url, status) VALUES (%s, %s, %s, %s, %s)"
            dbres = db_execute_query(query=query, params=results, isbulk=True, retrival=True)
            if not dbres:
                return "Status not updated"
            query = "UPDATE requests \
                    SET status = %s , completed_at = %s \
                        WHERE request_id = %s"
            params = ("completed", datetime.datetime.now(), str(request_id))
            res = db_execute_query(query=query,params=params,retrival=True)
            if not res:
                return "Status not updated"
    except Exception as e:
        print(f"Error processing CSV: {e}")
    return request_id


def process_image(url, request_id, product_name, result_queue):
    try:
        response = requests.get(url)
        img = Image.open(io.BytesIO(response.content))

        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        
        original_container_name = 'originalimages' 
        compressed_container_name = 'compressedimages' 
        
        original_container_client = blob_service_client.get_container_client(original_container_name)
        compressed_container_client = blob_service_client.get_container_client(compressed_container_name)

        original_blob_name = f"{request_id}/{product_name}/original_{uuid.uuid4()}.jpg"
        original_output = io.BytesIO()
        img.save(original_output, format='JPEG')
        original_output.seek(0)
        
        original_blob_client = original_container_client.get_blob_client(original_blob_name)
        original_blob_client.upload_blob(original_output, overwrite=True)
        original_url = original_blob_client.url

        compressed_output = io.BytesIO()
        compressed_img = img.copy()
        compressed_img.save(compressed_output, format='JPEG', quality=50)
        compressed_output.seek(0)

        compressed_blob_name = f"{request_id}/{product_name}/compressed_{uuid.uuid4()}.jpg"
        compressed_blob_client = compressed_container_client.get_blob_client(compressed_blob_name)
        compressed_blob_client.upload_blob(compressed_output, overwrite=True)
        compressed_url = compressed_blob_client.url

        result_queue.put((product_name, url, original_url, compressed_url))

        print(f"Processed image for {product_name}, original URL: {original_url}, compressed URL: {compressed_url}")
        return original_url, compressed_url

    except Exception as e:
        print(f"Error processing image {url}: {e}")
        return None, None
    

def check_status(request_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT status FROM requests WHERE request_id = %s', (str(request_id),))
        result = cursor.fetchone()
        return result[0] if result else 'Not Found'
    except Exception as e:
        print(f"Error checking status: {e}")
        return 'Error'
    finally:
        cursor.close()
        conn.close()


def get_data_db(request_id):
    try:
        query = """SELECT i.product_name,\
                    STRING_AGG(i.original_url, ', ') AS input_image_urls,\
                    STRING_AGG(i.compressed_url, ', ') AS output_image_urls \
                FROM image i JOIN requests r ON r.request_id = i.request_id \
                WHERE r.status = 'completed' AND r.request_id = %s \
                GROUP BY i.product_name"""
        params = (str(request_id),)

        dbres = db_execute(query=query,retval=True,single=False,params=params)
        if not dbres:
            print(f"No data found for request_id: {(request_id)}")
            return []
        results = [
            {
                'product_name': row[0],
                'input_image_urls': row[1],
                'output_image_urls': row[2]
            } 
            for row in dbres
        ]
        
        data = pd.DataFrame(results)
        return data
    except Exception as e:
        print(f"Error checking status: {e}")
        return None