# PowerView

## Setup your environnement

Install necessary packages

```shell
sudo apt install python3.13-venv rename podman podman-compose podman-docker
```

Enable podman service

```shell
systemctl --user enable --now podman.socket
```

Verify if service is running

```shell
curl -H "Content-Type: application/json" --unix-socket /var/run/user/$UID/podman/podman.sock http://localhost/_ping
```

Clone this repository

```shell
cd /srv/
git clone https://github.com/fheslouin/powerview.git
```

Create a virtual env

```shell
python3 -m venv /srv/powerview/envs/powerview
```

Enable the virtual env

```shell
source /srv/powerview/envs/powerview/bin/activate
```

Install python dependencies

```shell
pip install -r requirements.txt
```

## Deploy Grafana & InfluxDB

### Create .env file

Copy the file to create a new one and fill it up with your own information

```shell
cp .env.sample .env
```

* Set a `GRAFANA_PASSWORD`
* Set `INFLUXDB_ADMIN_TOKEN` you can retrieve it from the influxdb web interface

### Start Grafana and InfluxDB

```shell
podman compose up -d
```

### Install Caddy on host

#### Add repository and install Caddy package

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

#### Create Caddy config

```shell
cat << EOF | sudo tee /etc/caddy/Caddyfile
{
    # Set your ACME contact email for Let's Encrypt notifications
    email contact@adecwatts.fr
}

ftp.powerview.adecwatts.fr {
    # Reverse proxy to FTP service running on port 8080
    reverse_proxy localhost:8080
}

powerview.adecwatts.fr {
    # Reverse proxy to grafana service running on port 3000
    reverse_proxy localhost:8088
}

db.powerview.adecwatts.fr {
    # Reverse proxy to influxdb service running on port 8086
    reverse_proxy localhost:8086
}
EOF
```

#### Apply changes

Restart Caddy to apply changes

```shell
sudo systemctl restart caddy
```

### Install SFTPGo server

Install the server

```shell
sudo add-apt-repository ppa:sftpgo/sftpgo
sudo apt install sftpgo
 ```

Verify it runs correctly

```shell
systemctl status sftpgo
```

Create a config file to trigger a script each time a TSV file or folder are uploded

```shell
cat << EOF | sudo tee /etc/sftpgo/sftpgo.env
SFTPGO_COMMON__ACTIONS__EXECUTE_ON=mkdir
SFTPGO_COMMON__ACTIONS__HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMON__POST_DISCONNECT_HOOK=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__PATH=/srv/powerview/on-upload.sh
SFTPGO_COMMAND__COMMANDS__0__ENV=SFTPGO_ACTION=upload
SFTPGO_COMMAND__COMMANDS__0__HOOK=post_disconnect
EOF
```

Restart sftpgo to apply changes

```shell
systemctl restart sftpgo
```

Create a log directory where logs from `on-upload.sh` will be written (nice and easy way to see what is happening each time a file is pushed on the ftp server)

```shell
mkdir /srv/sftpgo/logs
```

Folders and files inside this path `/srv/sftpgo` and `/srv/powerview` must be owned by sftpgo user to works correctly

Set owner on `/srv/`

```shell
chown -R sftpgo:sftpgo /srv/
```

We need also our ubuntu user to have ownership for convenience. To do so we will use `acl`

Install `acl` package

```shell
sudo apt install acl
```

and add ownership for ubuntu user on `/srv` sub-folders

```shell
sudo setfacl -d -R -m u:ubuntu:rwx /srv/
sudo chown -R sftpgo:sftpgo /srv/
```

## Security

Enable firewall and allow traffics over 443 and 2022 ports

```shell
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw limit ssh
sudo ufw allow https
sudo ufw allow 2022
sudo ufw enable
```

Grafana, InfluxDB and FTP Webservice are available behind Caddy reverse proxy on 443
sFTP service is available over 2022 port

## General workflow

We now have Grafana, Influxdb and a FTP server running.

Head to

* Grafana : http://powerview.adecwatts.fr:8088/ and create an admin user.
* Sftpgo : http://powerview.adecwatts.fr:8080/ and create an admin user. Then create a user (it can be "company1")

For each uploaded file, it will be pushed in the tree shown below

```bash
├── /srv/sftpgo/data/
│   ├── company1
│   │   └── campaign1
│   │       └── 02001084
│   │           ├── T302_251012_031720.tsv
│   │           ├── T302_251013_031720.tsv
│   │           ├── T302_251014_031719.tsv
│   │           ├── T302_251015_031739.tsv
│   │           ├── T302_251016_031719.tsv
│   │           ├── T302_251017_031740.tsv
│   │           ├── T302_251018_031739.tsv
│   │           ├── T302_251019_031739.tsv
│   │           ├── T302_251020_031500.tsv
│   │           └── T302_251021_031740.tsv
│   └── compagny2
│       └── capaign23
│           └── 02001084
│               └── T302_251021_031740.tsv
```

data / client_name / campaign_name / device_serial_number / \*.tsv files

SFTPGo creates event that are catch each time a file is uploaded or a directory is created.
`on-upload.sh` react on this event and does two things :

* on `upload` it runs the `tsv_parser.py` python script that mainly parse the `.tsv` file and inject the data in influxDB
* on `mkdir` (when the MV2 creates a new campaign directory) it runs an ansible playbook (stored in `grafana-automation`). This playbook will automatically create Grafana resources based on the tree above
  * Create a Team (get from `data / client_name`)
  * Create a folder associated to the Team (get from `data / client_name / campaign_name`)
  * Create an InfluxDB resource connected to the `client_name` database
  * Export the Master Grafana Dashboard used as a reference (can be seen in Grafana in : Dashboard -> admin -> Master)
  * Modify and import the newly created dashboard in the Team folder
  * Set permissions on the dashboard and the team folder for the created Team

Once done, you'll be able to see a new Dashboard created in Grafana based on data imported by the python parser.

## Divers

### Grafana Flux query

To get all channels as a dashboard variables

```flux
from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "${__dashboard.name}")
  |> filter(fn: (r) => r.unit == "W")
  |> map(fn: (r) => ({
      _field: "SN: " + string(v: r.device) + " - Ch: " + string(v: r.channel_name)
  }))
  |> distinct(column: "_field")
  |> sort(columns: ["_field"])
```

To get a time series from the selected channels

```flux
from(bucket: v.defaultBucket)
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "${__dashboard.name}")
  |> map(fn: (r) => ({
      _time: r._time,
      _value: r._value,
      _field: "SN: " + string(v: r.device) + " - Ch: " + string(v: r.channel_name)
  }))
  |> filter(fn: (r) => contains(value: r._field, set: ${channels:json}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "multi_sn_channel")
```