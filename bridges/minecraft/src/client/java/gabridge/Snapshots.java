package gabridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.Window;
import net.minecraft.client.Camera;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.ItemStack;
import java.util.Set;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;
import org.joml.Vector3fc;

/** One frame's state for the assistant (Minecraft's own coordinates: blocks, Y up, +X east, +Z south). */
final class Snapshots {
	/** Characters within this many blocks of the player are published. */
	static final double ENTITY_RANGE = 64.0;
	/** Blocks the walk may use up building (bridging a gap, pillaring up), as Baritone does. */
	static final Set<String> THROWAWAY = Set.of("minecraft:dirt", "minecraft:coarse_dirt", "minecraft:cobblestone",
			"minecraft:cobbled_deepslate", "minecraft:stone", "minecraft:netherrack", "minecraft:andesite",
			"minecraft:diorite", "minecraft:granite", "minecraft:deepslate", "minecraft:tuff", "minecraft:blackstone",
			"minecraft:end_stone", "minecraft:mud_bricks", "minecraft:mossy_cobblestone");

	private Snapshots() {
	}

	static JsonObject make(Minecraft mc, long frame) {
		JsonObject o = new JsonObject();
		o.addProperty("v", 1);
		o.addProperty("frame", frame);
		LocalPlayer p = mc.player;
		boolean single = Bridge.singleplayer(mc);
		o.addProperty("singleplayer", single);
		o.add("window", window(mc));
		if (p == null || mc.level == null || !single) {
			o.addProperty("in_world", false);       // (on a server it publishes nothing: single-player only)
			return o;
		}
		o.addProperty("in_world", true);
		o.addProperty("menu", mc.gui.screen() != null);
		o.addProperty("paused", mc.isPaused());
		o.addProperty("dimension", String.valueOf(mc.level.dimension().identifier()));
		o.add("player", player(p));
		o.add("world", world(mc, p));
		o.add("camera", camera(mc.gameRenderer.mainCamera()));
		o.add("target", target(mc, p));
		JsonObject screen = Crafting.screen(mc);
		if (screen != null) {
			o.add("container", screen);
		}
		JsonArray ents = new JsonArray();
		Vec3 at = p.position();
		for (Entity e : mc.level.entitiesForRendering()) {
			if (e == p || e.position().distanceTo(at) > ENTITY_RANGE) {
				continue;
			}
			ents.add(entity(e));
		}
		o.add("entities", ents);
		return o;
	}

	static JsonObject window(Minecraft mc) {
		Window w = mc.getWindow();
		JsonObject o = new JsonObject();
		o.addProperty("x", w.getX());
		o.addProperty("y", w.getY());
		o.addProperty("w", w.getWidth());              // framebuffer: physical pixels
		o.addProperty("h", w.getHeight());
		o.addProperty("sw", w.getScreenWidth());       // window size in screen units
		o.addProperty("sh", w.getScreenHeight());
		o.addProperty("focused", mc.isWindowActive());
		return o;
	}

	static JsonObject player(LocalPlayer p) {
		JsonObject o = new JsonObject();
		Vec3 pos = p.position();
		Vec3 eye = p.getEyePosition();
		o.add("pos", Bridge.vec(pos.x, pos.y, pos.z));
		o.add("eye", Bridge.vec(eye.x, eye.y, eye.z));
		o.addProperty("yaw", p.getYRot());
		o.addProperty("pitch", p.getXRot());
		o.addProperty("health", p.getHealth());
		o.addProperty("max_health", p.getMaxHealth());
		o.addProperty("food", p.getFoodData().getFoodLevel());
		o.addProperty("armor", p.getArmorValue());
		o.addProperty("air", p.getAirSupply());
		o.addProperty("dead", p.isDeadOrDying());
		o.addProperty("creative", p.isCreative());
		o.addProperty("spectator", p.isSpectator());
		// for fighting: how charged the next swing is (1: full damage), being hurt, on the ground, falling,
		// what's in the off hand (a shield), whether it's held up
		o.addProperty("attack_strength", p.getAttackStrengthScale(0.0f));
		o.addProperty("attack_delay", p.getCurrentItemAttackStrengthDelay());
		o.addProperty("hurt_time", p.hurtTime);
		o.addProperty("on_ground", p.onGround());
		o.addProperty("vy", p.getDeltaMovement().y);
		ItemStack off = p.getOffhandItem();
		o.addProperty("offhand", off.isEmpty() ? "" : String.valueOf(BuiltInRegistries.ITEM.getKey(off.getItem())));
		o.addProperty("blocking", p.isBlocking());
		ItemStack held = p.getMainHandItem();
		if (!held.isEmpty()) {
			o.addProperty("item", String.valueOf(BuiltInRegistries.ITEM.getKey(held.getItem())));
			o.addProperty("item_name", held.getHoverName().getString());
			o.addProperty("item_count", held.getCount());
		}
		// the hotbar: what's in each slot, which is selected, and which holds blocks to build with
		Inventory inv = p.getInventory();
		JsonArray bar = new JsonArray();
		int blockSlot = -1, blocks = 0;
		for (int i = 0; i < 9; i++) {
			ItemStack st = inv.getItem(i);
			String id = st.isEmpty() ? "" : String.valueOf(BuiltInRegistries.ITEM.getKey(st.getItem()));
			JsonArray one = new JsonArray();
			one.add(id);
			one.add(st.getCount());
			bar.add(one);
			if (st.getItem() instanceof BlockItem && (THROWAWAY.contains(id) || id.endsWith("_planks"))) {
				blocks += st.getCount();
				if (blockSlot < 0) {
					blockSlot = i;
				}
			}
		}
		JsonObject counts = new JsonObject();               // everything carried: item id -> how many
		for (int i = 0; i < 36; i++) {
			ItemStack st = inv.getItem(i);
			if (!st.isEmpty()) {
				String id = String.valueOf(BuiltInRegistries.ITEM.getKey(st.getItem()));
				counts.addProperty(id, (counts.has(id) ? counts.get(id).getAsInt() : 0) + st.getCount());
			}
		}
		o.add("inventory", counts);
		int free = 0;
		for (int i = 0; i < 36; i++) {
			free += inv.getItem(i).isEmpty() ? 1 : 0;
		}
		o.addProperty("free_slots", free);
		o.addProperty("slot", inv.getSelectedSlot());
		o.add("hotbar", bar);
		o.addProperty("block_slot", blockSlot);
		o.addProperty("blocks", p.isCreative() ? 999 : blocks);
		return o;
	}

	/** Where the player is: biome, time of day (0 = sunrise, 6000 noon, 13000 night), weather, light. */
	static JsonObject world(Minecraft mc, LocalPlayer p) {
		JsonObject o = new JsonObject();
		BlockPos at = p.blockPosition();
		o.addProperty("biome", mc.level.getBiome(at).getRegisteredName());
		long t = mc.level.getOverworldClockTime();
		o.addProperty("time_of_day", Math.floorMod(t, 24000L));
		o.addProperty("day", Math.floorDiv(t, 24000L) + 1);
		o.addProperty("raining", mc.level.isRaining());
		o.addProperty("thundering", mc.level.isThundering());
		o.addProperty("light", mc.level.getMaxLocalRawBrightness(at));
		o.addProperty("sky_darken", mc.level.getSkyDarken());
		return o;
	}

	static JsonObject camera(Camera c) {
		JsonObject o = new JsonObject();
		Vec3 pos = c.position();
		o.add("pos", Bridge.vec(pos.x, pos.y, pos.z));
		o.add("fwd", v(c.forwardVector()));
		o.add("up", v(c.upVector()));
		o.add("left", v(c.leftVector()));
		o.addProperty("fov", c.getFov());
		o.addProperty("yaw", c.yRot());
		o.addProperty("pitch", c.xRot());
		return o;
	}

	static JsonArray v(Vector3fc a) {
		return Bridge.vec(a.x(), a.y(), a.z());
	}

	/** What the crosshair is on, as the game itself decides (an entity, or a block and its face). */
	static JsonObject target(Minecraft mc, LocalPlayer p) {
		JsonObject o = new JsonObject();
		HitResult hit = mc.hitResult;
		if (hit == null || hit.getType() == HitResult.Type.MISS) {
			o.addProperty("kind", "none");
			return o;
		}
		Vec3 at = hit.getLocation();
		o.add("hit", Bridge.vec(at.x, at.y, at.z));
		if (hit instanceof EntityHitResult eh) {
			o.addProperty("kind", "entity");
			o.addProperty("id", eh.getEntity().getId());
		} else if (hit instanceof BlockHitResult bh) {
			BlockPos bp = bh.getBlockPos();
			BlockState st = mc.level.getBlockState(bp);
			o.addProperty("kind", "block");
			o.add("pos", Bridge.vec(bp.getX(), bp.getY(), bp.getZ()));
			o.addProperty("face", bh.getDirection().getSerializedName());
			o.addProperty("block", String.valueOf(BuiltInRegistries.BLOCK.getKey(st.getBlock())));
			o.addProperty("name", st.getBlock().getName().getString());
			// the hotbar slot that breaks it fastest (-1: none is faster than the hand)
			Inventory inv = p.getInventory();
			int best = -1;
			float fastest = 1.0f;
			for (int i = 0; i < 9; i++) {
				float speed = inv.getItem(i).getDestroySpeed(st);
				if (speed > fastest) {
					fastest = speed;
					best = i;
				}
			}
			o.addProperty("tool_slot", best);
		}
		return o;
	}

	static JsonObject entity(Entity e) {
		JsonObject o = new JsonObject();
		Vec3 pos = e.position();
		o.addProperty("id", e.getId());
		o.addProperty("type", String.valueOf(BuiltInRegistries.ENTITY_TYPE.getKey(e.getType())));
		o.addProperty("name", e.getName().getString());
		o.add("pos", Bridge.vec(pos.x, pos.y, pos.z));
		o.addProperty("w", e.getBbWidth());
		o.addProperty("h", e.getBbHeight());
		o.addProperty("yaw", e.getYRot());
		Vec3 look = e.getLookAngle();
		o.add("look", Bridge.vec(look.x, look.y, look.z));
		o.addProperty("hostile", e instanceof Enemy);
		if (e instanceof LivingEntity le2) {           // what it's doing (fighting: a bow drawn, arms up...)
			o.addProperty("using_item", le2.isUsingItem());
			o.addProperty("blocking", le2.isBlocking());
		}
		if (e instanceof net.minecraft.world.entity.Mob mob) {
			o.addProperty("aggressive", mob.isAggressive());
		}
		if (e instanceof net.minecraft.world.entity.monster.Creeper c) {
			o.addProperty("swell", c.getSwelling(0.0f));  // 0..1: about to explode
		}
		if (e instanceof net.minecraft.world.entity.item.ItemEntity it) {   // an item lying on the ground
			o.addProperty("item", String.valueOf(BuiltInRegistries.ITEM.getKey(it.getItem().getItem())));
			o.addProperty("item_count", it.getItem().getCount());
		}
		o.addProperty("living", e instanceof LivingEntity);
		boolean dead = !e.isAlive() || e.isRemoved();
		if (e instanceof LivingEntity le) {
			dead = dead || le.isDeadOrDying();
			o.addProperty("hp", le.getHealth());
			o.addProperty("max_hp", le.getMaxHealth());
			o.addProperty("hurt", le.hurtTime);
		}
		o.addProperty("dead", dead);
		return o;
	}
}
