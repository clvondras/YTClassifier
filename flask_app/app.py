from flask import Flask, redirect, url_for, render_template, request, flash
from flask.ext.orientdb import OrientDB
from apiclient.discovery import build
from apiclient.errors import HttpError
import json
import requests
import math

app = Flask(__name__)

client = OrientDB(app=app, server_un='root', server_pw='password')
db_name = 'db4'
db_type = 'plocal'
client.set_db(db_name)

youtube = build("youtube", "v3", developerKey="AIzaSyCNK9sszpTTfHt-ypPDs3B2BVttkC_cgLU")

# creates database
@app.route('/')
def index():
    if not client.db_exists(db_name, db_type):
        client.db_create(db_name, 'graph', db_type)
        with client.connection():
            client.command("CREATE CLASS User EXTENDS V")
            client.command("CREATE CLASS Topic EXTENDS V")
            client.command("CREATE CLASS SubscribesTo EXTENDS E")
            client.command("CREATE CLASS UserTopic EXTENDS E")
            client.command("CREATE CLASS Gender EXTENDS V")
            client.command("CREATE CLASS UserGender EXTENDS E")
            client.command("CREATE VERTEX Gender SET type = 'Male'")
            client.command("CREATE VERTEX Gender SET type = 'Female'")
            #client.command("CREATE VERTEX Gender SET type = 'Unclassifiable'")

    return render_template('index.html')

# gets a channel's subscriptions without adding them to the database
def getSubscriptionsNoDB(channel_id):
    subs = []
    try:
        subscriptions_response = youtube.subscriptions().list(part="snippet", channelId=channel_id, maxResults=50).execute()
    except:
        return subs

    for result in subscriptions_response.get("items"):
        if goodSubscriberCount(result["snippet"]["resourceId"]["channelId"]) == False:
            continue
        subs.append(result["snippet"]["resourceId"]["channelId"])

    while len(str(subscriptions_response.get("nextPageToken"))) > 4: #convoluted way to check for next page token
        subscriptions_response = youtube.subscriptions().list(part="snippet", channelId=channel_id,
                                                              maxResults=50, pageToken=subscriptions_response.get("nextPageToken")).execute()
        subs += [result["snippet"]["resourceId"]["channelId"] for result in subscriptions_response.get('items') if goodSubscriberCount(result["snippet"]["resourceId"]["channelId"])]                
    return subs

# classifies user's gender (you can ignore everything else)
@app.route('/query', methods=['GET', 'POST'])
def subsQuery():
    subs = getSubscriptionsNoDB(request.form['user'])
    gender_count = []
    dbtotal = 0

    if request.method=='POST':
        with client.connection():
            q = client.query("SELECT type, COUNT(type) AS count FROM (SELECT expand(out('UserGender')) FROM (SELECT expand(in('SubscribesTo')) FROM User WHERE channelId=\'%s\')) GROUP BY type" % request.form['user'])
            g =  client.query("SELECT expand(out('UserGender').type) FROM User WHERE channelId=\'%s\'" % request.form['user'])

        if len(g) > 1:
            user_gender = "Gender Neutral"
        elif len(g) == 1:
            user_gender = g[0].value

        elif not subs:
            user_gender = "Unclassifiable, no subscriptions"
        else:
            user_gender = "(Not in database) " + classifyGender(request.form['user'], subs)
        try:
            channel_response= youtube.channels().list(part="snippet, statistics", id=request.form['user']).execute()
            title = channel_response["items"][0]["snippet"]["title"]
            if channel_response["items"][0]["statistics"]["hiddenSubscriberCount"] == True:
                total="Subscriber Count Hidden"
            else:
                total = format(int(channel_response["items"][0]["statistics"]["subscriberCount"]), ",d")

            for i in q:
                dbtotal += i.count
                gender_count.append((i.type, i.count))
        except:
            title=dbtotal=total=user_gender="User does not exist."

    return render_template('userquery.html', user=request.form['user'], title=title, dbtotal=dbtotal, total=total, counts=gender_count, user_gender=user_gender)

# adds a user and their subscriptions to the database
def getUsers(channel_id):
    with client.connection():
        q = "SELECT * FROM User WHERE channelId = \'%s\'" % channel_id
        try:
            if client.query(q)[0].checked == 1:
                return
            else:
                client.command("UPDATE User SET checked=1 WHERE channelId = \'%s\'" % channel_id)
        except:
            client.command("CREATE VERTEX User SET channelId = \'%s\', checked=1" % channel_id)
       
    return getSubscriptions(channel_id)
    
# checks that a channel has at least 500 subscribers
def goodSubscriberCount(channel_id):
    subscriber_count= youtube.channels().list(part="statistics", id=channel_id).execute()
    try:
        if subscriber_count["items"][0]["statistics"]["hiddenSubscriberCount"] == False:
            if int(subscriber_count["items"][0]["statistics"]["subscriberCount"]) > 500:
                return True
    except:
        pass
    return False

# gets a channel's subscriptions from getSubscriptionsNoDB(channel_id), then adds them to the database
def getSubscriptions(channel_id):
    subs = getSubscriptionsNoDB(channel_id)
    if not subs:
        return subs
    
    for sub in subs:
        with client.connection():
            try:
                if client.query("SELECT checked FROM User WHERE channelId = \'%s\'" % sub)[0].checked == 1:
                    pass
            except:
                client.command("CREATE VERTEX User SET channelId = \'%s\', checked=0" % sub)

            client.command("CREATE EDGE SubscribesTo FROM (SELECT FROM User WHERE channelId=\'%s\') TO (SELECT FROM User WHERE channelId=\'%s\')" % (channel_id, sub))
            
    return subs

# classifies gender of user given their subscriptions
def classifyGender(channel_id, subs):
    subs_f = subs_m = 1
    if len(subs) < 10:
        return "Unclassifiable, less than 10 subscriptions"
    with client.connection():
        try:
            # checks if they have been classified already... maybe not necessary
            if client.query("SELECT out('UserGender').size() AS gender FROM User WHERE channelId=\'%s\'" % channel_id)[0].gender > 0:
                return
        except:
            pass

        # number of females and males in the database
        f_degree = client.query("SELECT in('UserGender').size() AS degree FROM Gender WHERE type = 'Female'")[0].degree
        m_degree = client.query("SELECT in('UserGender').size() AS degree FROM Gender WHERE type = 'Male'")[0].degree
    total = f_degree + m_degree
    
    for sub in subs:
        with client.connection():
            try:
                # counts the number of females/males who subscribe to a user
                female_subscribers = client.query("SELECT COUNT(channelId) AS count FROM (SELECT expand(in('UserGender').out('SubscribesTo')) FROM Gender WHERE type = 'Female') WHERE channelId = \'%s\'" % sub)[0].count
                male_subscribers = client.query("SELECT COUNT(channelId) AS count FROM (SELECT expand(in('UserGender').out('SubscribesTo')) FROM Gender WHERE type = 'Male') WHERE channelId = \'%s\'" % sub)[0].count
                subs_f += math.log10((1 + female_subscribers) / (f_degree + 1))
                subs_m += math.log10((1 + male_subscribers) / (m_degree + 1))
            except:
                continue

    p_female = f_degree / (total + 1)
    p_male = m_degree / (total + 1)
    
    f = subs_f * p_female
    m = subs_m * p_male

    print(f)
    print(m)

    if f > m:
        return "Female"
        
    elif m > f:
        return "Male"
        
    return "Unclassifiable"

@app.route('/train')
def training():
    with client.connection():
        q = client.query("SELECT channelId from User where checked=0 limit 1")

    try:
        channel_response = youtube.channels().list(part="snippet", id=q[0].channelId).execute()
        
        title = channel_response["items"][0]["snippet"]["title"]
        descript = channel_response["items"][0]["snippet"]["description"]
        if descript == "":
            descript = "Description not available."
        pic = channel_response["items"][0]["snippet"]["thumbnails"]["default"]["url"]
        return render_template('train.html', title=channel_response["items"][0]["snippet"]["title"], descript=descript, id=q[0].channelId, pic=pic)
    except:
        return render_template('train.html', title="No users to train.", descript="Add users on the home page :)", id="", pic="")

# buttons
@app.route('/train/male/<channel_id>')
def male(channel_id):
    try:
        getUsers(channel_id)
        with client.connection():
            if client.query("SELECT out('UserGender').size() AS gender FROM User WHERE channelId=\'%s\'" % channel_id)[0].gender == 0:
                client.command("CREATE EDGE UserGender FROM (SELECT FROM User WHERE channelId = \'%s\') TO (SELECT FROM Gender WHERE type='Male')" % channel_id)
    except:
        pass
    return redirect(url_for('training'))


@app.route('/train/female/<channel_id>')
def female(channel_id):
    try:
        getUsers(channel_id)
        with client.connection():
            if client.query("SELECT out('UserGender').size() AS gender FROM User WHERE channelId=\'%s\'" % channel_id)[0].gender == 0:
                client.command("CREATE EDGE UserGender FROM (SELECT FROM User WHERE channelId = \'%s\') TO (SELECT FROM Gender WHERE type='Female')" % channel_id)
    except:
        pass
    return redirect(url_for('training'))


@app.route('/train/neutral/<channel_id>')
def neutral(channel_id):
    try:
        getUsers(channel_id)
        with client.connection():
            if client.query("SELECT out('UserGender').size() AS gender FROM User WHERE channelId=\'%s\'" % channel_id)[0].gender == 0:
                client.command("CREATE EDGE UserGender FROM (SELECT FROM User WHERE channelId = \'%s\') TO (SELECT FROM Gender WHERE type='Female')" % channel_id)
                client.command("CREATE EDGE UserGender FROM (SELECT FROM User WHERE channelId = \'%s\') TO (SELECT FROM Gender WHERE type='Male')" % channel_id)
    except:
        pass
    return redirect(url_for('training'))

if __name__ == '__main__':
    app.debug = True
    app.run(host='0.0.0.0')
