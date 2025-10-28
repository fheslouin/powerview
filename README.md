# powerview

## Podman setup (Docker alternative)

> Podman is a daemonless, open source, Linux native tool designed to make it easy to find, run, build, share and deploy applications using Open Containers Initiative (OCI) Containers and Container Images

### Install podman
```
sudo apt install podman podman-compose podman-docker
systemctl --user enable --now podman.socket
```
Verify if service is running
```
curl -H "Content-Type: application/json" --unix-socket /var/run/user/$UID/podman/podman.sock http://localhost/_ping
```
## Deploy Grafana & InfluxDB

### Create .env file

```
mv .env.sample .env
```

```shell .env
INFLUXDB_URL=https://influxdb:8181
INFLUX_HOST=http://0.0.0.0:8181
INFLUXDB_ADMIN_TOKEN=xxxxxxxxxxx
SESSION_SECRET_KEY=xxxxxxxxxxxxxxx

GRAFANA_PASSWORD=xxxxxxxxxxxx
GRAFANA_URL=http://xxxxxxxxxxx:8088
```

Generate random session secret key

```
openssl rand -hex 32
```

* Set `SESSION_SECRET_KEY` var with the result
* Set a `GRAFANA_PASSWORD`
* Set `INFLUXDB_ADMIN_TOKEN` with InfluxDB 3 admin token

### Start the stack

```
podman compose up -d
```

In production setup, prefer to launch only influxdb and grafana and choose to not start influxdb explorer

```
podman compose up grafana influxdb
```


### Generate an influxdb3 admin token

Then generate an admin token

```
podman exec -ti influxdb influxdb3 create token --admin
```

Paste the given token in `INFLUXDB_ADMIN_TOKEN` env var in the `.env` file

## Import data

### Setup your environnement

Install python virtual env package
```
sudo apt install python3.13-venv rename
```

Create a virtual env
```
python3 -m venv ~/envs/powerview
```

Enable the virtual env
```
    source ~/envs/powerview/bin/activate
```

Install python dependencies
```
pip install -r requirements.txt
```

### Verify your data structure

```bash
├── data
│   ├── compagny1
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

data -> client_name -> campaign_name -> device_serial_number : \*.tsv files
### Launch data import

```
python3 tsv_parser.py data/
```

## Create a Grafana dashboard for a campaign

See : [README](grafana-automation/README.md)