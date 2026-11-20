"""A mapper's own content folders (added to gameinfo.txt's SearchPaths) are part of the game's content."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hammerless.core.gamematerials import base_texture, read_vmt
from hammerless.core.vpk import GameContent, search_folders, write_vpk

GAMEINFO = """"GameInfo"
{
	game	"Left 4 Dead 2"
	FileSystem
	{
		SteamAppId	550
		SearchPaths
		{
			Game				update
			Game				left4dead2_custom    // my stuff
			"Game"				"left4dead2_darkwood"
			Game				left4dead2_missing
			Game				|gameinfo_path|.
			Game				hl2
		}
	}
}
"""


def put(root, rel, data=b"x"):
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    return full


class SearchPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="hl_test_search_")
        self.root = self.tmp.name
        put(self.root, "left4dead2/gameinfo.txt", GAMEINFO.encode())
        os.makedirs(os.path.join(self.root, "update"))
        write_vpk(os.path.join(self.root, "left4dead2", "pak01_dir.vpk"), {
            "materials/concrete/floor.vmt": b'"LightmappedGeneric"\n{\n"$basetexture" "concrete/floor"\n}\n',
            "materials/shared/wall.vmt": b'"LightmappedGeneric"\n{\n"$basetexture" "shared/packed"\n}\n'})
        put(self.root, "left4dead2_custom/materials/Marcos/Brick.vmt",
            b'"LightmappedGeneric"\n{\n"$basetexture" "marcos/brick_d"\n}\n')
        put(self.root, "left4dead2_custom/materials/marcos/brick_d.vtf", b"VTF-data")
        put(self.root, "left4dead2_custom/materials/shared/wall.vmt",
            b'"LightmappedGeneric"\n{\n"$basetexture" "shared/custom"\n}\n')
        put(self.root, "left4dead2_darkwood/models/darkwood/tree.mdl", b"IDST")
        put(self.root, "left4dead2_darkwood/maps/darkwood_01.bsp", b"VBSP")

    def tearDown(self):
        self.tmp.cleanup()

    def test_folders_in_gameinfo_order(self):
        names = [os.path.basename(f) for f in search_folders(self.root)]
        self.assertEqual(names, ["update", "left4dead2_custom", "left4dead2_darkwood", "left4dead2"])

    def test_no_gameinfo_is_the_game_folder(self):
        os.remove(os.path.join(self.root, "left4dead2", "gameinfo.txt"))
        self.assertEqual([os.path.basename(f) for f in search_folders(self.root)], ["left4dead2"])
        self.assertTrue(GameContent(self.root).has_material("concrete/floor"))

    def test_custom_content_is_found(self):
        c = GameContent(self.root)
        self.assertTrue(c.has_material("marcos/brick"))
        self.assertTrue(c.has_material("Marcos\\Brick"))
        self.assertTrue(c.has_model("models/darkwood/tree.mdl"))
        self.assertFalse(c.has_material("marcos/nothing"))
        self.assertEqual(c.read("materials/marcos/brick_d.vtf"), b"VTF-data")
        self.assertEqual(c.read("models/darkwood/tree.mdl"), b"IDST")
        self.assertIn("marcos/brick", c.materials("marcos"))
        self.assertIn("maps/darkwood_01.bsp", c.files)
        self.assertEqual(base_texture(c, "marcos/brick", os.path.join(self.root, "left4dead2")), "marcos/brick_d")

    def test_packed_files_still_read_and_custom_wins(self):
        c = GameContent(self.root)
        self.assertEqual(read_vmt(c, "concrete/floor")["$basetexture"], "concrete/floor")
        self.assertEqual(read_vmt(c, "shared/wall")["$basetexture"], "shared/custom")

    def test_file_added_later_is_found(self):
        c = GameContent(self.root)
        put(self.root, "left4dead2_custom/materials/marcos/new.vmt", b'"UnlitGeneric"\n{\n}\n')
        self.assertTrue(c.has_material("marcos/new"))
        self.assertEqual(read_vmt(c, "marcos/new")["shader"], "unlitgeneric")

    def test_packed_custom_folder_outside_the_game(self):
        with tempfile.TemporaryDirectory(prefix="hl_test_search_out_") as out:
            write_vpk(os.path.join(out, "pak01_dir.vpk"), {"materials/far/away.vmt": b'"UnlitGeneric"\n{\n}\n'})
            text = GAMEINFO.replace("Game				hl2", 'Game				"%s"' % out.replace("\\", "/"))
            put(self.root, "left4dead2/gameinfo.txt", text.encode())
            self.assertTrue(GameContent(self.root).has_material("far/away"))


if __name__ == "__main__":
    unittest.main()
