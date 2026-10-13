package gabridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.Block;

import java.util.HashSet;
import java.util.Set;

/**
 * The nearest blocks of some kinds, in the loaded world round the player (read-only):
 *   {"op":"find_blocks","ids":["minecraft:stone","minecraft:cobblestone"],"max":8}
 *   -> {"found":[[x,y,z,"minecraft:stone"],...]} nearest first; {"op":"block_ids"} -> {"ids":[...]}.
 * Searched in growing cubes (8, 16, 32, 64 blocks out; 32 up and down at most) until something is found.
 */
final class Finder {
	static final int[] RADII = {8, 16, 32, 64};
	static final int DEPTH = 32;

	private Finder() {
	}

	static JsonObject ids() {
		JsonObject o = new JsonObject();
		JsonArray a = new JsonArray();
		BuiltInRegistries.BLOCK.keySet().forEach(k -> a.add(k.toString()));
		o.add("ids", a);
		return o;
	}

	static JsonObject find(Minecraft mc, JsonArray idList, int max) {
		JsonObject o = new JsonObject();
		ClientLevel level = mc.level;
		if (level == null || mc.player == null || !Bridge.singleplayer(mc)) {
			o.addProperty("error", "not in a single-player world");
			return o;
		}
		Set<Block> want = new HashSet<>();
		idList.forEach(e -> BuiltInRegistries.BLOCK.getOptional(net.minecraft.resources.Identifier.tryParse(
				e.getAsString())).ifPresent(want::add));
		JsonArray found = new JsonArray();
		if (want.isEmpty()) {
			o.add("found", found);
			o.addProperty("error", "no such block");
			return o;
		}
		BlockPos at = mc.player.blockPosition();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		int inner = -1;
		java.util.List<long[]> hits = new java.util.ArrayList<>();
		for (int r : RADII) {
			int ry = Math.min(r, DEPTH);
			for (int dy = -ry; dy <= ry; dy++) {
				for (int dz = -r; dz <= r; dz++) {
					for (int dx = -r; dx <= r; dx++) {
						if (Math.max(Math.abs(dx), Math.abs(dz)) <= inner && Math.abs(dy) <= Math.min(inner, DEPTH)) {
							continue;                     // looked at already
						}
						pos.set(at.getX() + dx, at.getY() + dy, at.getZ() + dz);
						if (!level.isLoaded(pos)) {
							continue;
						}
						if (want.contains(level.getBlockState(pos).getBlock())) {
							hits.add(new long[]{(long) dx * dx + (long) dy * dy + (long) dz * dz, pos.getX(), pos.getY(),
									pos.getZ()});
						}
					}
				}
			}
			inner = r;
			if (!hits.isEmpty()) {
				break;
			}
		}
		hits.sort((a, b) -> Long.compare(a[0], b[0]));
		for (int i = 0; i < Math.min(max, hits.size()); i++) {
			long[] h = hits.get(i);
			JsonArray one = new JsonArray();
			one.add(h[1]);
			one.add(h[2]);
			one.add(h[3]);
			pos.set(h[1], h[2], h[3]);
			one.add(String.valueOf(BuiltInRegistries.BLOCK.getKey(level.getBlockState(pos).getBlock())));
			found.add(one);
		}
		o.add("found", found);
		return o;
	}
}
