import typer
from click import Choice
from typer import Option

from basis.cli.config import update_local_basis_config, get_basis_config_path
from basis.cli.newapp import app
from basis.cli.services import auth
from basis.cli.services.api import exit_on_http_error
from basis.cli.services.list import list_organizations, list_environments
from basis.cli.services.output import print_success, print_info

_email_help = "The email address of the account"
_password_help = "The password for the account"


@app.command()
def login(
    email: str = Option(..., prompt=True, help=_email_help),
    password: str = Option(..., prompt=True, hide_input=True, help=_password_help),
):
    """Log in to your Basis account"""
    with exit_on_http_error("Login failed"):
        auth.login(email, password)

    with exit_on_http_error("Fetching organizations failed"):
        organizations = list_organizations()

    if len(organizations) == 1:
        org_name = organizations[0]["name"]
    else:
        org_name = typer.prompt(
            "Select an organization",
            type=Choice([org["name"] for org in organizations]),
        )

    with exit_on_http_error("Fetching environments failed"):
        environments = list_environments()

    if len(environments) == 1:
        env_name = environments[0]["name"]
    elif environments:
        env_name = typer.prompt(
            "Select an organization", type=Choice([env["name"] for env in environments])
        )
    else:
        env_name = None

    update_local_basis_config(organization_name=org_name, environment_name=env_name)
    print_success(f"\nLogged in to Basis organization {org_name} as {email}")
    print_info(f"\nYour login information is stored at {get_basis_config_path()}")
