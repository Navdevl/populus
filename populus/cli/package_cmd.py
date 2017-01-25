import click
import json
import os

from populus.utils.filesystem import (
    ensure_path_exists,
)
from populus.utils.packaging import (
    SUPPORTED_PACKAGE_MANIFEST_VERSIONS,
    validate_package_manifest,
    get_lockfile_build_path,
    validate_release_lockfile,
    get_publishable_backends,
)

from populus.packages.build import (
    persist_package_file,
    construct_release_lockfile,
)
from populus.packages.installation import (
    install_packages_to_project,
    update_project_dependencies,
)

from .main import main


@main.group('package')
@click.pass_context
def package_cmd(ctx):
    """
    Package management commands.
    """
    pass


def split_on_commas(values):
    return [value.strip() for value in values.split(',') if value]


@package_cmd.command('init')
@click.pass_context
def package_init(ctx):
    """
    Initialize the `epm.json` file.
    """
    project = ctx.obj['PROJECT']

    if project.has_package_manifest:
        overwrite_msg = (
            "An `epm.json` file is already present. If you proceed your choices "
            "will overwrite any existing values"
        )
        if not click.confirm(overwrite_msg, default=False):
            ctx.exit(1)
        package_manifest = project.package_manifest
    else:
        package_manifest = {}

    package_manifest.setdefault('manifest_version', '1')

    if package_manifest['manifest_version'] not in SUPPORTED_PACKAGE_MANIFEST_VERSIONS:
        raise ValueError(
            "Unsupported manifest version.  Supported versions are {0}".format(
                ", ".join(
                    version
                    for version
                    in sorted(SUPPORTED_PACKAGE_MANIFEST_VERSIONS)
                )
            )
        )

    if project.has_package_manifest:
        click.echo("Updating existing epm.json file.")
    else:
        click.echo("Writing new epm.json file.")

    # TODO: pull from git configuration if present.
    package_manifest['package_name'] = click.prompt(
        'Package Name',
        default=package_manifest.get('package_name', ''),
    )

    # TODO: pull default email from git configuration.
    package_manifest['authors'] = click.prompt(
        'Author(s)',
        value_proc=split_on_commas,
        default=package_manifest.get('authors', []),
    )

    package_manifest['version'] = click.prompt(
        'Version',
        default=package_manifest.get('version', '1.0.0'),
    )

    # TODO: auto detect this from a LICENSE file if present.
    package_manifest['license'] = click.prompt(
        'License',
        default=package_manifest.get('license', 'MIT'),
    )

    package_manifest['description'] = click.prompt(
        'Description',
        default=package_manifest.get('description', ''),
    )

    package_manifest['keywords'] = click.prompt(
        'Keywords',
        value_proc=split_on_commas,
        default=package_manifest.get('keywords', []),
    )

    package_manifest['links'] = click.prompt(
        'Links',
        value_proc=split_on_commas,
        default=package_manifest.get('links', {}),
    )

    with open(project.package_manifest_path, 'w') as package_manifest_file:
        json.dump(package_manifest, package_manifest_file, sort_keys=True, indent=2)

    click.echo("Wrote package manifest: {0}".format(project.package_manifest_path))


@package_cmd.command('install')
@click.argument('package_identifiers', nargs=-1)
@click.option('--save/--no-save', default=True, help="Save package into manifest dependencies")
@click.pass_context
def package_install(ctx, package_identifiers, save):
    """
    Install package(s).

    1. Load package manifest.

    TODO: figure out what the right steps are for this.  Should probably be a
    multi-phase thing which first resolves all of the identifiers, then
    resolves all dependencies for each identifier, then does the actual
    installation.
    """
    project = ctx.obj['PROJECT']

    if not package_identifiers:
        package_identifiers = ('.',)

    installed_dependencies = install_packages_to_project(
        project.installed_packages_dir,
        package_identifiers,
        project.package_backends,
    )
    click.echo("Installed Packages: {0}".format(', '.join((
        package_data['meta']['package_name'] for package_data in installed_dependencies
    ))))

    if save:
        update_project_dependencies(project, installed_dependencies)


@package_cmd.command('build')
@click.option(
    'chain_names',
    '--chain',
    '-c',
    multiple=True,
    help=(
        "Specifies which chains should be included in the deployments section "
        "of the release."
    ),
)
@click.option(
    'contract_instance_names',
    '--contract-instances',
    '-d',
    multiple=True,
    help=(
        "Specifies the deployed contract instances to include in the release."
    ),
)
@click.option(
    'contract_type_names',
    '--contract-types',
    '-t',
    multiple=True,
    help=(
        "Specifies the contract types to include in the release"
    ),
)
@click.option(
    '--overwrite/--no-overwrite',
    default=False,
    help=(
        "Specifies if this should overwrite any existing release lockfile"
    ),
)
@click.option('--wait-for-sync/--no-wait-for-sync', default=True)
@click.pass_context
def package_build(ctx,
                  chain_names,
                  contract_instance_names,
                  contract_type_names,
                  overwrite,
                  wait_for_sync):
    """
    Create a release.
    """
    project = ctx.obj['PROJECT']

    if not project.has_package_manifest:
        click.echo("No package manifest found in project.")
        ctx.exit(1)

    package_manifest = project.package_manifest
    validate_package_manifest(package_manifest)

    version = package_manifest['version']

    release_lockfile_path = get_lockfile_build_path(
        project.build_asset_dir,
        version,
    )

    if not overwrite and os.path.exists(release_lockfile_path):
        cannot_overwrite_msg = (
            "Found an existing release lockfile for {version} at "
            "{release_lockfile_path}.  Run command again with --overwrite to "
            "overwrite this file.".format(
                version=version,
                release_lockfile_path=release_lockfile_path,
            )
        )
        click.echo(cannot_overwrite_msg)
        ctx.exit(1)

    if chain_names and not contract_instance_names:
        click.echo("Must specify which contracts you want to include in the deployments")
        ctx.exit(1)

    release_lockfile = construct_release_lockfile(
        project=project,
        chain_names=chain_names,
        contract_instance_names=contract_instance_names,
        contract_type_names=contract_type_names,
    )

    validate_release_lockfile(release_lockfile)

    ensure_path_exists(project.build_asset_dir)

    with open(release_lockfile_path, 'w') as release_lockfile_file:
        json.dump(release_lockfile, release_lockfile_file, sort_keys=True, indent=2)

    click.echo("Wrote release lock file: {0}".format(release_lockfile_path))


@package_cmd.command('publish')
@click.argument(
    'release_lockfile_path',
    type=click.Path(
        exists=True,
        file_okay=True,
        dir_okay=False,
    ),
    nargs=1,
)
@click.option('--wait-for-sync/--no-wait-for-sync', default=True)
@click.pass_context
def package_publish(ctx, release_lockfile_path, wait_for_sync):
    """
    Create a release.
    """
    project = ctx.obj['PROJECT']

    if release_lockfile_path is None:
        # TODO: select from `./build` dir
        raise NotImplementedError("Not implemented")

    with open(release_lockfile_path) as release_lockfile_file:
        release_lockfile = json.load(release_lockfile_file)

    validate_release_lockfile(release_lockfile)

    package_backends = project.package_backends

    release_lockfile_uri = persist_package_file(release_lockfile_path, package_backends)
    publishable_backends = get_publishable_backends(
        release_lockfile,
        release_lockfile_uri,
        package_backends,
    )

    if not publishable_backends:
        raise ValueError("TODO: handle this gracefully")
    elif len(publishable_backends) > 1:
        raise ValueError("TODO: handle this gracefully")
    else:
        backend_name, backend = tuple(publishable_backends.items())[0]
        click.echo("Publishing to {0}".format(backend_name))
        backend.publish_release_lockfile(release_lockfile, release_lockfile_uri)
