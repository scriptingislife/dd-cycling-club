import os
import boto3
import requests
import urllib3
import logging
import json
import time
import sys

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.content_encoding import ContentEncoding
from datadog_api_client.v2.model.http_log import HTTPLog
from datadog_api_client.v2.model.http_log_item import HTTPLogItem

from datadog_api_client.v2.api.metrics_api import MetricsApi
from datadog_api_client.v2.model.metric_intake_type import MetricIntakeType
from datadog_api_client.v2.model.metric_payload import MetricPayload
from datadog_api_client.v2.model.metric_point import MetricPoint
from datadog_api_client.v2.model.metric_series import MetricSeries

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ENV = os.environ.get("ENV", "")
SERVICE = os.environ.get("SERVICE", "dd-cycling-club")
SOURCE = os.environ.get("SOURCE", "strava")
VERSION = os.environ.get("VERSION", "")

DD_API_KEY_PARAM = os.environ.get("DD_API_KEY_PARAM", "DDApiKey")
DD_CLUB_ID = os.environ.get("DD_CLUB_ID", None)
MEMBERS_METRIC = os.environ.get("MEMBERS_METRIC", "dd.cycling.club.members")
STRAVA_REFRESH_URI = "https://www.strava.com/api/v3/oauth/token"
CACHE_BUCKET_NAME = os.environ.get("CACHE_BUCKET_NAME", "dd-cycling-club")
CACHE_ACTIVITIES_KEY = os.environ.get("CACHE_ACTIVITIES_KEY", "activities.json")
if ENV != "":
    CACHE_ACTIVITIES_KEY = os.environ.get("CACHE_ACTIVITIES_KEY", f"activities-{ENV}.json")
CACHE_OAUTH_KEY = os.environ.get("CACHE_OAUTH_KEY", "oauth.json")
OAUTH_DATA = None
MAX_RETRIES = 3

if ENV == "staging":
    logging.getLogger().setLevel(logging.DEBUG)
else:
    logging.getLogger().setLevel(logging.INFO)

if DD_CLUB_ID is None:
    logging.error("Please provide the club ID in DD_CLUB_ID")
    sys.exit(1)

def get_s3_object(bucket, key):
    """Retrieve information stored in S3"""
    logging.debug(f"Fetching object {key} from S3 bucket {bucket}")
    logging.getLogger().setLevel(logging.INFO)
    try:
        s3 = boto3.resource('s3')
        obj = s3.Object(bucket, key)
        data = obj.get()['Body'].read().decode('utf-8')
        logging.getLogger().setLevel(logging.DEBUG)
        return data
    except Exception as e:
        logging.error(f"Error getting {key} from S3")
        logging.error(e)
        return None

def put_s3_object(bucket, key, data:str):
    """Save information to S3"""
    logging.debug(f"Uploading object {key} to S3 bucket {bucket}")
    try:
        s3 = boto3.resource('s3')
        obj = s3.Object(bucket, key)
        obj.put(Body=data.encode())
    except Exception as e:
        logging.error(f"Error uploading {key} to S3")
        logging.error(e)
        return None

def get_oauth_data():
    """Return OAuth credentials. Fetch from S3 if not stored locally."""
    if OAUTH_DATA is None:
        logging.debug(f"get_oauth_data - OAUTH_DATA is None")
        data = json.loads(get_s3_object(CACHE_BUCKET_NAME, CACHE_OAUTH_KEY))
        return data
    return OAUTH_DATA

def refresh_strava_token():
    """Update the OAuth access token and refresh token"""
    logging.debug("Refreshing Strava token if needed")

    # Fetch from S3 if None
    OAUTH_DATA = get_oauth_data()

    # Do not update if token not expired
    if int(OAUTH_DATA.get("expires_at", 0)) > int(time.time()):
        logging.debug("Access token not expired")
        return False

    if OAUTH_DATA is None:
        logging.debug("OAUTH_DATA is still None...")
        cached_oauth_data = json.loads(get_s3_object(CACHE_BUCKET_NAME, CACHE_OAUTH_KEY))
        if cached_oauth_data is None:
            logging.error("Error fetching OAuth data from S3")
            raise
        else:
            OAUTH_DATA = cached_oauth_data
    
    logging.debug("Setting up data for refresh request")
    data = {
        "client_id": OAUTH_DATA["client_id"],
        "client_secret": OAUTH_DATA["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": OAUTH_DATA["refresh_token"]
    }
    response = requests.post(STRAVA_REFRESH_URI, data=data)
    if response.status_code != 200:
        logging.error("Error refreshing OAuth data")
        logging.error(response.json())
        raise

    response_json = response.json()

    logging.debug("Saving new OAuth data")
    OAUTH_DATA["access_token"] = response_json["access_token"]
    OAUTH_DATA["refresh_token"] = response_json["refresh_token"]
    OAUTH_DATA["expires_at"] = response_json["expires_at"]
    put_s3_object(CACHE_BUCKET_NAME, CACHE_OAUTH_KEY, json.dumps(OAUTH_DATA))

    return True

def get_base_headers():
    """Return a base set of headers to be used for requests to Strava"""
    logging.debug("Getting base headers")
    global OAUTH_DATA
    if OAUTH_DATA is None:
        OAUTH_DATA = get_oauth_data()
    return {
        "Authorization": f"Bearer {OAUTH_DATA['access_token']}"
    }

def get_param(name):
    """Get an SSM parameter by name"""
    logging.debug("Fetching SSM parameter")
    ssm = boto3.client('ssm')
    parameter = ssm.get_parameter(Name=name, WithDecryption=True)
    return parameter['Parameter']['Value']

def send_dd_metric(name, value):
    """Send a metric value to Datadog"""
    logging.debug(f"Sending metric {name} with value {value}")
    body = MetricPayload(
        series=[
            MetricSeries(
                metric=name,
                type=MetricIntakeType.UNSPECIFIED,
                points=[
                    MetricPoint(
                        timestamp=int(time.time()),
                        value=value,
                    ),
                ],
            ),
        ],
    )

    configuration = Configuration()
    configuration.api_key["apiKeyAuth"] = get_param(DD_API_KEY_PARAM)
    with ApiClient(configuration) as api_client:
        api_instance = MetricsApi(api_client)
        response = api_instance.submit_metrics(body=body)
        logging.info(response)

    return response


def send_dd_log(payload):
    """Send a log to Datadog"""
    logging.debug("Submitting log: " + str(payload))
    body = HTTPLog(
        [
            HTTPLogItem(
                ddsource=SOURCE,
                ddtags=f"env:{ENV},version:{VERSION}",
                message=json.dumps(payload),
                service=SERVICE,
            ),
        ]
    )

    configuration = Configuration()
    configuration.api_key["apiKeyAuth"] = get_param(DD_API_KEY_PARAM)
    with ApiClient(configuration) as api_client:
        api_instance = LogsApi(api_client)
        response = api_instance.submit_log(content_encoding=ContentEncoding.DEFLATE, body=body)
        logging.info(response)

    return response

def get_club_member_total(id, per_page=30, retries=0):
    """Get the total number of members in a Strava club"""
    if retries >= MAX_RETRIES:
        # Raise error
        raise
    params = {
        "page": 1,
        "per_page": per_page
    }
    logging.debug(f"Strava /members params {str(params)}")
    members = []
    page_len = per_page
    while page_len == per_page:
        page = requests.get(f"https://www.strava.com/api/v3/clubs/{id}/members", headers=get_base_headers(), params=params)
        if page.status_code == 403:
            # Refresh access token
            refresh_strava_token()
            retries = retries + 1
        elif page.status_code != 200:
            # Raise error
            print(page.json())
            raise
        page_json = page.json()
        
        members = members + page_json
        
        page_len = len(page_json)
        params["page"] = params["page"] + 1
    return len(members)

def activities_are_same(x, y):
    """
    If a ride is renamed, the activities object changes.
    Compare attributes to determine if two activities with different names are the same.
    """
    if x["distance"] == y["distance"] and x["elapsed_time"] == y["elapsed_time"] and x["total_elevation_gain"] == y["total_elevation_gain"]:
        return True
    return False

def get_club_activities(id, per_page=30, retries=0):
    """Get the most recent Strava activities in a club"""
    if retries >= MAX_RETRIES:
        # Raise error
        raise
    params = {
        "per_page": per_page
    }
    page = requests.get(f"https://www.strava.com/api/v3/clubs/{id}/activities", headers=get_base_headers(), params=params)
    if page.status_code == 403:
        # Refresh access token
        refresh_strava_token()
        retries = retries + 1
        return get_club_activities(id, retries=retries)
    elif page.status_code != 200:
        # Raise error
        raise

    activities = page.json()
    logging.debug(f"Got {len(activities)} activities from Strava")
    s3_obj = get_s3_object(CACHE_BUCKET_NAME, CACHE_ACTIVITIES_KEY)
    # No old activities
    if s3_obj is None:
        new_activities = activities
    # Skip activities found in previous set
    else:
        old_activities = json.loads(s3_obj)
        new_activities = []
        # TODO: Is there a better algorithm?
        for activity in activities:
            duplicate_activity = False
            for old_activity in old_activities:
                if activities_are_same(activity, old_activity) == True:
                    duplicate_activity = True
                    break
            
            # Activity is not logged in previous set
            if duplicate_activity == False:
                logging.debug(f"New activity found")
                new_activities.append(activity)

    logging.info(f"Found {len(new_activities)} new activities")

    # Only update activities file if there are changes
    if len(new_activities) > 0:
        logging.debug("Uploading activities")
        put_s3_object(CACHE_BUCKET_NAME, CACHE_ACTIVITIES_KEY, json.dumps(activities))
    
    return new_activities

def activities(event, context):
    """API endpoint to upload Strava activities to Datadog"""
    logging.debug("Fetching new club activities")
    new_activities = get_club_activities(DD_CLUB_ID)
    for activity in new_activities:
        send_dd_log(activity)
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "synced",
            "new_activities": len(new_activities)
        })
    }

def members(event, context):
    """API endpoint to upload the number of club members to Datadog"""
    logging.debug("Fetching club member total")
    total = get_club_member_total(DD_CLUB_ID)
    logging.info(f"Got member total: {total}")
    send_dd_metric(MEMBERS_METRIC, total)
    logging.debug("Returning total in HTTP response")
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "success",
            "members": total
        })
    }

if __name__ == "__main__":
    members(None, None)
    activities(None, None)