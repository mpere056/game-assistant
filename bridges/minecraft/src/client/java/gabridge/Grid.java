package gabridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.Fallable;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.material.FluidState;
import net.minecraft.world.phys.shapes.VoxelShape;

import java.util.Base64;
import java.util.Set;

/**
 * What each block in a box is, for walking: one byte per block, x fastest, then z, then y
 * ({"op":"grid","min":[x,y,z],"size":[sx,sy,sz]} -> {"min":..,"size":..,"data":base64,"hard":base64}). Read from the
 * client's own world (loaded chunks only; unloaded ones read as UNKNOWN). "hard", for breaking: how long the
 * block takes to break with the best tool in the hotbar, in units of 4 ticks (0-126, as the game reckons it:
 * tool speed / hardness / 30, or / 100 without the right tool; 127: can't be broken, or too slow to), plus 128
 * when it falls once what holds it goes (sand, gravel).
 */
final class Grid {
	static final byte AIR = 0;         // nothing to stand in or on
	static final byte WATER = 1;       // water: swim through it
	static final byte DANGER = 2;      // lava, fire, magma, cactus, berry bushes, cobwebs: keep off
	static final byte SOLID = 3;       // a full block: stand on top (y + 1)
	static final byte LOW = 4;         // a low collision (slab, carpet, snow layers): stand on it (y + top)
	static final byte TALL = 5;        // taller than a block (fences, walls): can't step over
	static final byte CLIMB = 6;       // ladder, vines, scaffolding
	static final byte DOOR = 7;        // doors, gates, trapdoors (closed or open)
	static final byte PARTIAL = 8;     // stairs and other shapes up to a block: stand on top, a step up
	static final byte UNKNOWN = 9;     // not loaded
	static final byte POWDER = 10;     // powder snow: sinks you, but can be waded through

	static final int MAX_BLOCKS = 160 * 1024;
	static final Set<String> DANGERS = Set.of("minecraft:lava", "minecraft:fire", "minecraft:soul_fire",
			"minecraft:magma_block", "minecraft:cactus", "minecraft:sweet_berry_bush", "minecraft:campfire",
			"minecraft:soul_campfire", "minecraft:wither_rose", "minecraft:cobweb",
			"minecraft:pointed_dripstone");

	/** Not the land: what grows on it (passed over for the ground's height). */
	static final Set<String> TREE_PARTS = Set.of("minecraft:mushroom_stem", "minecraft:brown_mushroom_block",
			"minecraft:red_mushroom_block", "minecraft:bamboo", "minecraft:cactus", "minecraft:sugar_cane",
			"minecraft:bee_nest", "minecraft:cocoa", "minecraft:mangrove_roots", "minecraft:moss_carpet",
			"minecraft:snow");

	private Grid() {
	}

	static JsonObject read(Minecraft mc, JsonArray min, JsonArray size) {
		JsonObject o = new JsonObject();
		ClientLevel level = mc.level;
		if (level == null || mc.player == null || !Bridge.singleplayer(mc)) {
			o.addProperty("error", "not in a single-player world");
			return o;
		}
		int x0 = min.get(0).getAsInt(), y0 = min.get(1).getAsInt(), z0 = min.get(2).getAsInt();
		int sx = size.get(0).getAsInt(), sy = size.get(1).getAsInt(), sz = size.get(2).getAsInt();
		if ((long) sx * sy * sz > MAX_BLOCKS || sx <= 0 || sy <= 0 || sz <= 0) {
			o.addProperty("error", "box too big (at most " + MAX_BLOCKS + " blocks)");
			return o;
		}
		byte[] out = new byte[sx * sy * sz];
		byte[] hard = new byte[sx * sy * sz];
		java.util.List<ItemStack> tools = new java.util.ArrayList<>();
		for (int s = 0; s < 9; s++) {
			tools.add(mc.player.getInventory().getItem(s));
		}
		boolean creative = mc.player.isCreative();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		int i = 0;
		for (int y = 0; y < sy; y++) {
			for (int z = 0; z < sz; z++) {
				for (int x = 0; x < sx; x++) {
					pos.set(x0 + x, y0 + y, z0 + z);
					boolean loaded = level.isLoaded(pos);
					out[i] = loaded ? kind(level, pos) : UNKNOWN;
					hard[i++] = loaded ? breakTime(level, pos, tools, creative) : (byte) 127;
				}
			}
		}
		o.add("min", min);
		o.add("size", size);
		o.addProperty("data", Base64.getEncoder().encodeToString(out));
		o.addProperty("hard", Base64.getEncoder().encodeToString(hard));
		return o;
	}

	/** Tools whose digging time the estimate also gives (what one could make first). */
	static final String[] WOULD_HAVE = {"minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:iron_pickaxe",
			"minecraft:wooden_shovel", "minecraft:wooden_axe"};

	/**
	 * How long digging the straight way from one point to another would take ({"op":"dig_estimate","from":[x,y,z],
	 * "to":[x,y,z]}): the blocks in the way (2 high: the body), the seconds with the hotbar's best tools, and with
	 * each of WOULD_HAVE besides, and the commonest kinds of block. A guide for whether to make a tool first.
	 */
	static JsonObject digEstimate(Minecraft mc, JsonArray from, JsonArray to) {
		JsonObject o = new JsonObject();
		ClientLevel level = mc.level;
		if (level == null || mc.player == null || !Bridge.singleplayer(mc)) {
			o.addProperty("error", "not in a single-player world");
			return o;
		}
		java.util.List<ItemStack> tools = new java.util.ArrayList<>();
		for (int s = 0; s < 9; s++) {
			tools.add(mc.player.getInventory().getItem(s));
		}
		double x0 = from.get(0).getAsDouble(), y0 = from.get(1).getAsDouble(), z0 = from.get(2).getAsDouble();
		double x1 = to.get(0).getAsDouble(), y1 = to.get(1).getAsDouble(), z1 = to.get(2).getAsDouble();
		double len = Math.sqrt((x1 - x0) * (x1 - x0) + (y1 - y0) * (y1 - y0) + (z1 - z0) * (z1 - z0));
		int steps = Math.max(1, (int) Math.ceil(len * 3));
		java.util.Set<Long> seen = new java.util.HashSet<>();
		java.util.Map<String, Integer> kinds = new java.util.HashMap<>();
		double now = 0;
		double[] would = new double[WOULD_HAVE.length];
		int count = 0;
		boolean unknown = false;
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		for (int i = 0; i <= steps; i++) {
			double k = (double) i / steps;
			for (int up = 0; up < 2; up++) {
				pos.set(Math.floor(x0 + (x1 - x0) * k), Math.floor(y0 + (y1 - y0) * k) + up, Math.floor(z0 + (z1 - z0) * k));
				if (!seen.add(pos.asLong())) {
					continue;
				}
				if (!level.isLoaded(pos)) {
					unknown = true;
					continue;
				}
				BlockState st = level.getBlockState(pos);
				if (st.isAir() || st.getCollisionShape(level, pos).isEmpty()) {
					continue;
				}
				float h = st.getDestroySpeed(level, pos);
				if (h < 0) {
					continue;
				}
				count++;
				String id = String.valueOf(BuiltInRegistries.BLOCK.getKey(st.getBlock()));
				kinds.merge(id, 1, Integer::sum);
				now += ticks(st, h, tools);
				for (int w = 0; w < WOULD_HAVE.length; w++) {
					java.util.List<ItemStack> with = new java.util.ArrayList<>(tools);
					with.add(new ItemStack(BuiltInRegistries.ITEM.getValue(net.minecraft.resources.Identifier.parse(WOULD_HAVE[w]))));
					would[w] += ticks(st, h, with);
				}
			}
		}
		o.addProperty("blocks", count);
		o.addProperty("seconds_now", Math.round(now / 20.0));
		JsonObject w = new JsonObject();
		for (int i = 0; i < WOULD_HAVE.length; i++) {
			w.addProperty(WOULD_HAVE[i], Math.round(would[i] / 20.0));
		}
		o.add("seconds_with", w);
		JsonObject top = new JsonObject();
		kinds.entrySet().stream().sorted((a, b) -> b.getValue() - a.getValue()).limit(5)
				.forEach(e -> top.addProperty(e.getKey(), e.getValue()));
		o.add("kinds", top);
		o.addProperty("partly_unloaded", unknown);
		return o;
	}

	/**
	 * The height of the top of each column in a rectangle ({"op":"heights","min":[x,z],"size":[sx,sz]} ->
	 * {"heights":[...]} x fastest): the first air above the highest block that stops movement, i.e. where the
	 * open sky starts (the surface). ground: the land's own height instead, under the trees and plants (logs,
	 * leaves, big mushrooms, and whatever has nothing to stand on are passed over down to the first other block, or
	 * water): "take me to the ground" from a tree top was "already on the surface", 2026-10-13.
	 */
	static JsonObject heights(Minecraft mc, JsonArray min, JsonArray size, boolean ground) {
		JsonObject o = new JsonObject();
		ClientLevel level = mc.level;
		if (level == null || !Bridge.singleplayer(mc)) {
			o.addProperty("error", "not in a single-player world");
			return o;
		}
		int x0 = min.get(0).getAsInt(), z0 = min.get(1).getAsInt();
		int sx = Math.min(size.get(0).getAsInt(), 256), sz = Math.min(size.get(1).getAsInt(), 256);
		JsonArray h = new JsonArray();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
		for (int z = 0; z < sz; z++) {
			for (int x = 0; x < sx; x++) {
				int top = level.getHeight(net.minecraft.world.level.levelgen.Heightmap.Types.MOTION_BLOCKING, x0 + x, z0 + z);
				if (ground) {
					int y = top - 1;
					while (y > level.getMinY()) {
						pos.set(x0 + x, y, z0 + z);
						BlockState st = level.getBlockState(pos);
						if (!st.getFluidState().isEmpty() || !(st.is(BlockTags.LEAVES) || st.is(BlockTags.LOGS)
								|| TREE_PARTS.contains(String.valueOf(BuiltInRegistries.BLOCK.getKey(st.getBlock())))
								|| st.getCollisionShape(level, pos).isEmpty())) {
							break;
						}
						y--;
					}
					top = y + 1;
				}
				h.add(top);
			}
		}
		o.addProperty("ground", ground);
		o.add("min", min);
		o.add("size", size);
		o.add("heights", h);
		return o;
	}

	static double ticks(BlockState st, float h, java.util.List<ItemStack> tools) {
		if (h == 0) {
			return 1;
		}
		boolean needsTool = st.requiresCorrectToolForDrops();
		double best = 1.0 / h / (needsTool ? 100.0 : 30.0);
		for (ItemStack t : tools) {
			if (t.isEmpty()) {
				continue;
			}
			boolean right = !needsTool || t.isCorrectToolForDrops(st);
			best = Math.max(best, t.getDestroySpeed(st) / h / (right ? 30.0 : 100.0));
		}
		return Math.ceil(1.0 / best) + 6;            // (and a moment to aim at the next)
	}

	static byte breakTime(ClientLevel level, BlockPos pos, java.util.List<ItemStack> tools, boolean creative) {
		BlockState st = level.getBlockState(pos);
		float h = st.getDestroySpeed(level, pos);
		int v;
		if (h < 0) {
			v = 127;
		} else if (h == 0 || creative) {
			v = 0;
		} else {
			boolean needsTool = st.requiresCorrectToolForDrops();
			double best = 1.0 / h / (needsTool ? 100.0 : 30.0);      // by hand
			for (ItemStack t : tools) {
				if (t.isEmpty()) {
					continue;
				}
				boolean right = !needsTool || t.isCorrectToolForDrops(st);
				best = Math.max(best, t.getDestroySpeed(st) / h / (right ? 30.0 : 100.0));
			}
			double ticks = Math.ceil(1.0 / best);
			v = ticks > 126 * 4 ? 127 : (int) Math.ceil(ticks / 4.0);
		}
		if (st.getBlock() instanceof Fallable) {
			v |= 128;
		}
		return (byte) v;
	}

	static byte kind(ClientLevel level, BlockPos pos) {
		BlockState st = level.getBlockState(pos);
		FluidState fl = st.getFluidState();
		if (!fl.isEmpty() && fl.is(FluidTags.LAVA)) {
			return DANGER;
		}
		String id = String.valueOf(BuiltInRegistries.BLOCK.getKey(st.getBlock()));
		if (id.equals("minecraft:powder_snow")) {
			return POWDER;
		}
		if (DANGERS.contains(id)) {
			return DANGER;
		}
		if (st.is(BlockTags.CLIMBABLE)) {
			return CLIMB;
		}
		if (st.is(BlockTags.DOORS) || st.is(BlockTags.FENCE_GATES) || st.is(BlockTags.TRAPDOORS)) {
			return DOOR;
		}
		VoxelShape shape = st.getCollisionShape(level, pos);
		if (shape.isEmpty()) {
			return !fl.isEmpty() && fl.is(FluidTags.WATER) ? WATER : AIR;
		}
		double top = shape.max(Direction.Axis.Y);
		if (top > 1.01) {
			return TALL;
		}
		if (top <= 0.55) {
			return LOW;
		}
		return top >= 0.99 ? SOLID : PARTIAL;
	}
}
