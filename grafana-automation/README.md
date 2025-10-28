# Grafana Automation with Ansible

This Ansible playbook automates Grafana user management, team creation, datasource configuration, and dashboard imports.

## Features

- ✅ Creates non-admin users with auto-generated passwords
- ✅ Creates teams and assigns users
- ✅ Creates dedicated folders per team
- ✅ Configures InfluxDB 3 Core datasources
- ✅ Imports and templates dashboards dynamically
- ✅ Sets proper permissions (team as viewer)
- ✅ Supports resource deletion

## Prerequisites

- Ansible Core 2.19 / Ansible 12
- Python 3.9+
- Access to a Grafana instance
- InfluxDB 3 Core instance

## Installation

1. Install required collections:

```
cd grafana-automation/
ansible-galaxy collection install -r requirements.yml
```

## Usage

### Create user, team and Grafana dashboard

Source environment variables

```
export $(cat ../.env)
source ~/envs/powerview/bin/activate
```

Run the playbook

```
ansible-playbook playbooks/create_grafana_resources.yml
```

You will be prompted for:
- Username
- Email address
- Company name
- Campaign name

The password will be automatically generated and displayed at the end.

### Delete user, team and Grafana dashboard

Source environment variables

```
export $(cat ../.env)
source ~/envs/powerview/bin/activate
```

Run the deletion playbook

```
ansible-playbook playbooks/delete_grafana_resources.yml
```

You will be prompted for :
* User name
* Company name

Currently it delete only the user created along user / team / dashboard creation. You will have clean up if other users have been created meanwhile