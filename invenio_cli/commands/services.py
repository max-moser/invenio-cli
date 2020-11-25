# -*- coding: utf-8 -*-
#
# Copyright (C) 2020 CERN.
#
# Invenio-Cli is free software; you can redistribute it and/or modify it
# under the terms of the MIT License; see LICENSE file for more details.

"""Invenio module to ease the creation and management of applications."""

from ..helpers.docker_helper import DockerHelper
from ..helpers.process import ProcessResponse
from .commands import Commands
from .services_health import HEALTHCHECKS, ServicesHealthCommands
from .steps import CommandStep, FunctionStep


class ServicesCommands(Commands):
    """Service CLI commands."""

    def __init__(self, cli_config, docker_helper=None):
        """Constructor."""
        super(ServicesCommands, self).__init__(cli_config)
        self.docker_helper = docker_helper or \
            DockerHelper(cli_config.get_project_shortname(), local=True)

    def ensure_containers_running(self):
        """Ensures containers are running."""
        project_shortname = self.cli_config.get_project_shortname()

        self.docker_helper.start_containers()

        ServicesHealthCommands.wait_for_services(
            services=["redis", self.cli_config.get_db_type(), "es"],
            project_shortname=project_shortname,
        )
        return ProcessResponse(
                output="Containers start and checkup run.",
                status_code=0,
        )

    def services_expected_status(self, expected):
        """Checks if the services are on the expected setup status."""
        if not self.cli_config.get_services_setup() == expected:
            return ProcessResponse(
                error=f"Services status inconistent. Expected {expected}" +
                      f" obtained {not expected}",
                status_code=1
            )

        return ProcessResponse(
                output=f"Services status conistent.",
                status_code=0
            )

    def _cleanup(self):
        """Services cleanup steps."""
        steps = [
            FunctionStep(func=self.services_expected_status,
                args={"expected": True},
                message="Checking services are setup..."
            ),
            CommandStep(cmd=[
                'pipenv', 'run', 'invenio', 'shell', '--no-term-title', '-c',
                "import redis; redis.StrictRedis.from_url(app.config['CACHE_REDIS_URL']).flushall(); print('Cache cleared')"],  # noqa
                env={'PIPENV_VERBOSITY': "-1"},
                message="Flushing Redis..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'db', 'destroy',
                     '--yes-i-know'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Destroying database..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'index', 'destroy',
                     '--force', '--yes-i-know'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Destroying indices..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'index', 'queue',
                     'init', 'purge'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Purging queues..."
            ),
            FunctionStep(func=self.cli_config.update_services_setup,
                args={"is_setup": False},
                message="Updating service setup status (False)..."
            )
        ]

        return steps

    def _setup(self):
        """Services initialization steps."""
        steps = [
            FunctionStep(
                func=self.services_expected_status,
                args={"expected": False},
                message="Checking services are cleaned up..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'db', 'init', 'create'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Creating database..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'files', 'location',
                     'create', '--default', 'default-location',
                     "{}/data".format(self.cli_config.get_instance_path())],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Creating files location..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'roles', 'create', 'admin'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Creating admin role..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'access', 'allow',
                     'superuser-access', 'role', 'admin'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Allowing superuser access to admin role..."
            ),
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'index', 'init'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Creating indices..."
            ),
            FunctionStep(
                func=self.cli_config.update_services_setup,
                args={"is_setup": True},
                message="Updating service setup status (True)..."
            )
        ]

        return steps

    def demo(self):
        """Steps to add demo records into the instance."""
        steps = [
            CommandStep(
                cmd=['pipenv', 'run', 'invenio', 'rdm-records', 'demo'],
                env={'PIPENV_VERBOSITY': "-1"},
                message="Creatin demo records..."
            )
        ]

        return steps

    def setup(self, force, demo_data=True, stop=False, services=True):
        """Steps to setup services' containers.

        A check in invenio-cli's config file is done to see if one-time setup
        has been executed before.
        """
        steps = []

        if services:
            steps.append(
                FunctionStep(func=self.ensure_containers_running,
                             message="Making sure containers are up...")
            )
        if force:
            steps.extend(self._cleanup())

        steps.extend(self._setup())

        if demo_data:
            steps.extend(self.demo())

        if stop:
            steps.append(
                FunctionStep(
                    func=self.docker_helper.stop_containers,
                    message="Stopping containers...."
                )
            )
        return steps

    def start(self):
        """Steps to start services' containers."""
        steps = [
            FunctionStep(func=self.ensure_containers_running,
                         message="Making sure containers are up...")
        ]

        return steps

    def stop(self):
        """Stops containers."""
        steps = [
            FunctionStep(
                func=self.docker_helper.stop_containers,
                message="Stopping containers..."
            )
        ]

        return steps

    def destroy(self):
        """Steps to destroy the services's containers."""
        steps = [
            FunctionStep(
                func=self.docker_helper.destroy_containers,
                message="Destroying containers..."
            ),
            FunctionStep(
                func=self.cli_config.update_services_setup,
                args={"is_setup": False},
                message="Updating service setup status (False)..."
            )
        ]

        return steps

    def status(self, services, verbose):
        """Checks the status of the given service.

        :returns: A list of the same length than services. Each item will be a
                  code corresponding to: 0 success, 1 failure, 2 healthcheck
                  not defined.
        """
        project_shortname = self.cli_config.get_project_shortname()
        statuses = []
        for service in services:
            check = HEALTHCHECKS.get(service)
            if check:
                result = check(
                    filepath="docker-services.yml",
                    verbose=verbose,
                    project_shortname=project_shortname,
                )
                # Append 0 if OK, else 1
                # FIXME: Deal with codes higher than 1. Needed?
                code = 0 if result.status_code == 0 else 1
                statuses.append(code)
            else:
                statuses.append(2)

        return statuses