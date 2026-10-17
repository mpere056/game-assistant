package gabridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.chunk.LevelChunkSection;
import net.minecraft.world.level.chunk.status.ChunkStatus;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Predicate;

/**
 * The nearest blocks of some kinds, in the loaded world round the player (read-only):
 *   {"op":"find_blocks","ids":["minecraft:stone","minecraft:cobblestone"],"max":8}
 *   -> {"found":[[x,y,z,"minecraft:stone"],...]} nearest first;
 *   {"op":"find_each","ids":[...],"max":3} -> {"found":{"minecraft:poppy":[[x,y,z],...],...}}: the nearest of each
 *   kind, in one search (a plan weighs many: a bed's wool and dyes, each kind searched on its own, froze the game);
 *   {"op":"block_ids"} -> {"ids":[...]}.
 * Searched in growing cubes (8, 16, 32, 64 blocks out; 32 up and down at most) until something is found. A chunk
 * section (16 x 16 x 16) whose palette can't hold any of the kinds is passed over whole.
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
		return find(mc, idList, max, 0);
	}

	private static Set<Block> blocks(JsonArray idList) {
		Set<Block> want = new HashSet<>();
		idList.forEach(e -> BuiltInRegistries.BLOCK.getOptional(net.minecraft.resources.Identifier.tryParse(
				e.getAsString())).ifPresent(want::add));
		return want;
	}

	private interface Hit {
		void at(int x, int y, int z, Block b);
	}

	/** Every block of the kinds within r across and ry up and down of at, but those within inner (and innerY). */
	private static void scan(ClientLevel level, BlockPos at, int r, int ry, int inner, int innerY, Set<Block> want,
			Hit hit) {
		Predicate<BlockState> p = s -> want.contains(s.getBlock());
		int ax = at.getX(), ay = at.getY(), az = at.getZ();
		int y0 = Math.max(level.getMinY(), ay - ry);
		int y1 = Math.min(level.getMinY() + level.getHeight() - 1, ay + ry);
		if (y0 > y1) {
			return;
		}
		for (int cx = (ax - r) >> 4; cx <= (ax + r) >> 4; cx++) {
			for (int cz = (az - r) >> 4; cz <= (az + r) >> 4; cz++) {
				LevelChunk ch = level.getChunkSource().getChunk(cx, cz, ChunkStatus.FULL, false);
				if (ch == null) {
					continue;
				}
				for (int si = level.getSectionIndex(y0); si <= level.getSectionIndex(y1); si++) {
					LevelChunkSection sec = ch.getSection(si);
					if (sec == null || sec.hasOnlyAir() || !sec.maybeHas(p)) {
						continue;
					}
					int by = level.getSectionYFromSectionIndex(si) << 4;
					for (int ly = 0; ly < 16; ly++) {
						int y = by + ly;
						if (y < y0 || y > y1) {
							continue;
						}
						for (int lz = 0; lz < 16; lz++) {
							int z = (cz << 4) + lz;
							if (Math.abs(z - az) > r) {
								continue;
							}
							for (int lx = 0; lx < 16; lx++) {
								int x = (cx << 4) + lx;
								if (Math.abs(x - ax) > r) {
									continue;
								}
								if (Math.max(Math.abs(x - ax), Math.abs(z - az)) <= inner && Math.abs(y - ay) <= innerY) {
									continue;             // looked at already
								}
								Block b = sec.getBlockState(lx, ly, lz).getBlock();
								if (want.contains(b)) {
									hit.at(x, y, z, b);
								}
							}
						}
					}
				}
			}
		}
	}

	/** radius > 0: all of that radius is searched (else out in rings, stopping at the first with any). */
	static JsonObject find(Minecraft mc, JsonArray idList, int max, int radius) {
		JsonObject o = new JsonObject();
		ClientLevel level = mc.level;
		if (level == null || mc.player == null || !Bridge.singleplayer(mc)) {
			o.addProperty("error", "not in a single-player world");
			return o;
		}
		Set<Block> want = blocks(idList);
		JsonArray found = new JsonArray();
		if (want.isEmpty()) {
			o.add("found", found);
			o.addProperty("error", "no such block");
			return o;
		}
		BlockPos at = mc.player.blockPosition();
		int inner = -1, innerY = -1;
		List<long[]> hits = new ArrayList<>();
		Map<Long, Block> kinds = new HashMap<>();
		int[] radii = radius > 0 ? new int[]{Math.min(radius, 64)} : RADII;
		for (int r : radii) {
			int ry = Math.min(r, DEPTH);
			scan(level, at, r, ry, inner, innerY, want, (x, y, z, b) -> {
				long dx = x - at.getX(), dy = y - at.getY(), dz = z - at.getZ();
				hits.add(new long[]{dx * dx + dy * dy + dz * dz, x, y, z});
				kinds.put(BlockPos.asLong(x, y, z), b);
			});
			inner = r;
			innerY = ry;
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
			one.add(String.valueOf(BuiltInRegistries.BLOCK.getKey(kinds.get(BlockPos.asLong((int) h[1], (int) h[2],
					(int) h[3])))));
			found.add(one);
		}
		o.add("found", found);
		return o;
	}

	/** The nearest few of each kind, in one search out in rings (a kind found stops being looked for). */
	static JsonObject findEach(Minecraft mc, JsonArray idList, int max) {
		JsonObject o = new JsonObject();
		ClientLevel level = mc.level;
		if (level == null || mc.player == null || !Bridge.singleplayer(mc)) {
			o.addProperty("error", "not in a single-player world");
			return o;
		}
		Set<Block> want = blocks(idList);
		BlockPos at = mc.player.blockPosition();
		Map<Block, List<long[]>> hits = new HashMap<>();
		int inner = -1, innerY = -1;
		for (int r : RADII) {
			if (want.isEmpty()) {
				break;
			}
			int ry = Math.min(r, DEPTH);
			Map<Block, List<long[]>> ring = new HashMap<>();
			scan(level, at, r, ry, inner, innerY, want, (x, y, z, b) -> {
				long dx = x - at.getX(), dy = y - at.getY(), dz = z - at.getZ();
				ring.computeIfAbsent(b, k -> new ArrayList<>()).add(new long[]{dx * dx + dy * dy + dz * dz, x, y, z});
			});
			for (Map.Entry<Block, List<long[]>> e : ring.entrySet()) {
				hits.computeIfAbsent(e.getKey(), k -> new ArrayList<>()).addAll(e.getValue());
				want.remove(e.getKey());
			}
			inner = r;
			innerY = ry;
		}
		JsonObject found = new JsonObject();
		for (Map.Entry<Block, List<long[]>> e : hits.entrySet()) {
			List<long[]> l = e.getValue();
			l.sort((a, b) -> Long.compare(a[0], b[0]));
			JsonArray arr = new JsonArray();
			for (int i = 0; i < Math.min(max, l.size()); i++) {
				JsonArray one = new JsonArray();
				one.add(l.get(i)[1]);
				one.add(l.get(i)[2]);
				one.add(l.get(i)[3]);
				arr.add(one);
			}
			found.add(String.valueOf(BuiltInRegistries.BLOCK.getKey(e.getKey())), arr);
		}
		o.add("found", found);
		return o;
	}
}
