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
