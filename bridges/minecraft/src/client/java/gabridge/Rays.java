package gabridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Rays through the blocks (not entities), as the game itself casts them: through their collision shapes, or with
 * outline, their outlines, as the crosshair sees them (grass and flowers stop it: a crafting table's spot was "in
 * sight" through grass the crosshair stopped on, 2026-10-13). Each hit: [1, x, y, z, face x, y, z, block id, block x,
 * y, z, replaceable (a block put there takes its place: grass), solid (it has a collision shape)].
 */
final class Rays {
	/** At most this many rays in one request (each is cheap; this only bounds a frame's extra work). */
	static final int MAX_RAYS = 512;

	private Rays() {
	}

	static JsonObject cast(Minecraft mc, JsonArray rays, boolean fluids, boolean outline) {
		JsonObject o = new JsonObject();
		JsonArray hits = new JsonArray();
		o.add("hits", hits);
		if (mc.level == null || mc.player == null || !Bridge.singleplayer(mc)) {
			o.addProperty("error", "not in a single-player world");
			return o;
		}
		int n = 0;
		for (JsonElement r : rays) {
			if (n++ >= MAX_RAYS) {
				break;
			}
			JsonArray a = r.getAsJsonArray();
			Vec3 from = new Vec3(a.get(0).getAsDouble(), a.get(1).getAsDouble(), a.get(2).getAsDouble());
			Vec3 to = new Vec3(a.get(3).getAsDouble(), a.get(4).getAsDouble(), a.get(5).getAsDouble());
			BlockHitResult h = mc.level.clip(new ClipContext(from, to,
					outline ? ClipContext.Block.OUTLINE : ClipContext.Block.COLLIDER,
					fluids ? ClipContext.Fluid.ANY : ClipContext.Fluid.NONE, mc.player));
			JsonArray row = new JsonArray();
			if (h == null || h.getType() == HitResult.Type.MISS) {
				row.add(0);
			} else {
				Vec3 at = h.getLocation();
				Direction d = h.getDirection();
				row.add(1);
				row.add(at.x);
				row.add(at.y);
				row.add(at.z);
				row.add(d.getStepX());
				row.add(d.getStepY());
				row.add(d.getStepZ());
				net.minecraft.core.BlockPos bp = h.getBlockPos();
				net.minecraft.world.level.block.state.BlockState st = mc.level.getBlockState(bp);
				row.add(String.valueOf(BuiltInRegistries.BLOCK.getKey(st.getBlock())));
				row.add(bp.getX());
				row.add(bp.getY());
				row.add(bp.getZ());
				row.add(st.canBeReplaced());
				row.add(!st.getCollisionShape(mc.level, bp).isEmpty());
			}
			hits.add(row);
		}
		return o;
	}
}
