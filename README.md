# dd-cycling-club

Log and visualize Strava activities with Datadog

## Deploy the application
1. Find your club's ID. This can be done by visiting the club's page and executing `console.log(club.id)` in the Developer Tools console or by making an authenticated request to the `/athlete/clubs` API endpoint.

2. Create a [SSM Parameter](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html) named `DDApiKey` containing a [Datadog API key](https://app.datadoghq.com/organization-settings/api-keys). If a different name is used for the parameter, update the `DDParamName` parameter in `template.yaml`

3. Deploy the SAM app, containing two Lambda functions and an S3 bucket

```
sam build
sam deploy --guided # Enter the club ID when prompted
```

Upload an S3 object called `oauth.json` with the following content. Fill in the fields with the information found on Strava's [My API Application](https://www.strava.com/settings/api) page.

```
{
    "client_id": "",
    "client_secret": "",
    "access_token": "",
    "refresh_token": ""
}
```

4. Invoke the functions manually or wait for them to run. Functions are invoked on schedules defined in the SAM template.

## To do
- [ ] SSM Parameter defined in SAM
- [ ] S3 `oauth.json` object defined in SAM
- [ ] Datadog config as code
- [ ] Detailed data using individual account authorization