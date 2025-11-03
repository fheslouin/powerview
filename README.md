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
cat <<EOF > /etc/sftpgo/sftpgo.env
SFTPGO_COMMON__ACTIONS__EXECUTE_ON=upload,mkdir
SFTPGO_COMMON__ACTIONS__HOOK=/srv/powerview/on-upload.sh
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
from(bucket: "enercoop")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "tertiaire")
  |> filter(fn: (r) => r.unit == "W")
  |> map(fn: (r) => ({
      _field: "SN: " + string(v: r.device) + " - Ch: " + string(v: r.channel_name)
  }))
  |> distinct(column: "_field")
  |> sort(columns: ["_field"])
```

To get a time series from the selected channels

```flux
from(bucket: "enercoop")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "tertiaire")
  |> map(fn: (r) => ({
      _time: r._time,
      _value: r._value,
      _field: "SN: " + string(v: r.device) + " - Ch: " + string(v: r.channel_name)
  }))
  |> filter(fn: (r) => contains(value: r._field, set: ${channels:json}))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
  |> yield(name: "multi_sn_channel")
```