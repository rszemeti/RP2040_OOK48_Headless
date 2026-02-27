Import("env")

from datetime import datetime
from pathlib import Path
import shutil

from SCons.Script import AlwaysBuild


def export_release_uf2(source, target, env):
    project_dir = Path(env.subst("$PROJECT_DIR"))
    build_dir = Path(env.subst("$BUILD_DIR"))
    env_name = env.subst("$PIOENV")

    source_uf2 = build_dir / "firmware.uf2"
    if not source_uf2.exists():
        print(f"[release-uf2] UF2 not found at: {source_uf2}")
        return 1

    release_dir = project_dir / "releases"
    release_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_uf2 = release_dir / f"{project_dir.name}-{env_name}-{timestamp}.uf2"
    shutil.copy2(source_uf2, output_uf2)

    print(f"[release-uf2] Exported: {output_uf2}")
    return 0


release_target = env.AddCustomTarget(
    name="release-uf2",
    dependencies=["$BUILD_DIR/${PROGNAME}.uf2"],
    actions=[export_release_uf2],
    title="Build release UF2",
    description="Build and copy UF2 into releases/",
)
AlwaysBuild(release_target)
