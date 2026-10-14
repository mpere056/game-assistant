package gabridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.CraftingContainer;
import net.minecraft.world.inventory.ResultSlot;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.ItemStackTemplate;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.RecipeHolder;
import net.minecraft.world.item.crafting.ShapedRecipe;
import net.minecraft.world.item.crafting.ShapelessRecipe;
import net.minecraft.world.item.crafting.SmeltingRecipe;
import net.minecraft.world.inventory.AbstractFurnaceMenu;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

/**
 * The game's own crafting knowledge, and the open crafting screen (read-only):
 *   {"op":"recipes"}  every shaped and shapeless crafting recipe: {"recipes":[{"id","result","count","w","h",
 *                     "cells":[[item ids that fit],...] (w*h, row by row; [] for an empty cell),"shapeless"}]}; and
 *                     the furnace's (smelting): one cell, "station":"minecraft:furnace", "time": ticks to cook one;
 *                     and "fuels": {item id: ticks it burns} (as the game's own furnace reckons them)
 *   {"op":"sources"}  what each block drops, and the least tool it needs for that (the game's loot tables, tried
 *                     with no tool, then each tool): {"sources":{"minecraft:stone":{"drops":["minecraft:cobblestone"],
 *                     "tool":"minecraft:wooden_pickaxe"},...}}
 * and, in each snapshot while a container screen is open (screen()), its slots: where each is on the window (in
 * framebuffer pixels), what's in it, and what it is (the crafting grid, the result, the player's own slots).
 */
final class Crafting {
	/** The tools tried for drops, the least first (a block's tool is the first of these that gets drops). */
	static final String[] TOOLS = {"", "minecraft:wooden_axe", "minecraft:wooden_shovel", "minecraft:wooden_pickaxe",
			"minecraft:stone_pickaxe", "minecraft:iron_pickaxe", "minecraft:diamond_pickaxe", "minecraft:shears"};

	private Crafting() {
	}

	static String id(Item item) {
		return String.valueOf(BuiltInRegistries.ITEM.getKey(item));
	}

	static JsonArray ids(Ingredient ing) {
		JsonArray a = new JsonArray();
		ing.items().forEach(h -> a.add(h.getRegisteredName()));
		return a;
	}

	static Object field(Object o, String name) throws Exception {
		Class<?> c = o.getClass();
		while (c != null) {
			try {
				Field f = c.getDeclaredField(name);
				f.setAccessible(true);
				return f.get(o);
			} catch (NoSuchFieldException e) {
				c = c.getSuperclass();
			}
		}
		throw new NoSuchFieldException(name);
	}

	static JsonObject recipes() throws Exception {
		MinecraftServer server = Places.server();
		return server.submit(() -> {
			JsonObject o = new JsonObject();
			JsonArray all = new JsonArray();
			for (RecipeHolder<?> h : server.getRecipeManager().getRecipes()) {
				try {
					JsonObject r = new JsonObject();
					Object v = h.value();
					ItemStackTemplate result;
					if (v instanceof ShapedRecipe s) {
						result = (ItemStackTemplate) field(s, "result");
						r.addProperty("w", s.getWidth());
						r.addProperty("h", s.getHeight());
						JsonArray cells = new JsonArray();
						for (Optional<Ingredient> ing : s.getIngredients()) {
							cells.add(ing.isPresent() ? ids(ing.get()) : new JsonArray());
						}
						r.add("cells", cells);
						r.addProperty("shapeless", false);
					} else if (v instanceof ShapelessRecipe s) {
						result = (ItemStackTemplate) field(s, "result");
						JsonArray cells = new JsonArray();
						for (Ingredient ing : s.placementInfo().ingredients()) {
							cells.add(ids(ing));
						}
						r.add("cells", cells);
						r.addProperty("w", cells.size());
						r.addProperty("h", 1);
						r.addProperty("shapeless", true);
					} else if (v instanceof SmeltingRecipe s) {   // the furnace: one in, one out, in its time
						result = (ItemStackTemplate) field(s, "result");
						JsonArray cells = new JsonArray();
						cells.add(ids(s.input()));
						r.add("cells", cells);
						r.addProperty("w", 1);
						r.addProperty("h", 1);
						r.addProperty("shapeless", true);
						r.addProperty("station", "minecraft:furnace");
						r.addProperty("time", s.cookingTime());
					} else {
						continue;
					}
					r.addProperty("id", String.valueOf(h.id().identifier()));
					r.addProperty("result", result.item().getRegisteredName());
					r.addProperty("count", result.count());
					all.add(r);
				} catch (Exception e) {
					// a recipe of another shape: skip it
				}
			}
			o.add("recipes", all);
			o.add("fuels", fuels(server));
			return o;
		}).get(10, TimeUnit.SECONDS);
	}

	/** Each item that burns in a furnace, and for how many ticks (the game's own furnace asked, item by item). */
	static JsonObject fuels(MinecraftServer server) {
		JsonObject out = new JsonObject();
		try {
			ServerLevel level = server.overworld();
			net.minecraft.world.level.block.entity.FurnaceBlockEntity f =
					new net.minecraft.world.level.block.entity.FurnaceBlockEntity(BlockPos.ZERO,
							net.minecraft.world.level.block.Blocks.FURNACE.defaultBlockState());
			java.lang.reflect.Method burn = net.minecraft.world.level.block.entity.AbstractFurnaceBlockEntity.class
					.getDeclaredMethod("getBurnDuration", ServerLevel.class, ItemStack.class);
			burn.setAccessible(true);
			for (Item item : BuiltInRegistries.ITEM) {
				ItemStack st = new ItemStack(item);
				if (!st.has(net.minecraft.core.component.DataComponents.COOKING_FUEL)) {
					continue;
				}
				int ticks = (Integer) burn.invoke(f, level, st);
				if (ticks > 0) {
					out.addProperty(id(item), ticks);
				}
			}
		} catch (Exception e) {
			out.addProperty("error", String.valueOf(e));
		}
		return out;
	}

	static JsonObject sources() throws Exception {
		MinecraftServer server = Places.server();
		return server.submit(() -> {
			JsonObject o = new JsonObject();
			JsonObject all = new JsonObject();
			List<ServerPlayer> players = server.getPlayerList().getPlayers();
			if (players.isEmpty()) {
				o.addProperty("error", "no player");
				return o;
			}
			ServerPlayer p = players.get(0);
			ServerLevel level = p.level();
			BlockPos at = p.blockPosition();
			ItemStack[] tools = new ItemStack[TOOLS.length];
			for (int i = 0; i < TOOLS.length; i++) {
				tools[i] = TOOLS[i].isEmpty() ? ItemStack.EMPTY : new ItemStack(BuiltInRegistries.ITEM.getValue(
						net.minecraft.resources.Identifier.parse(TOOLS[i])));
			}
			for (Block b : BuiltInRegistries.BLOCK) {
				BlockState st = b.defaultBlockState();
				if (st.isAir()) {
					continue;
				}
				for (int i = 0; i < tools.length; i++) {
					try {
						if (st.requiresCorrectToolForDrops() && (tools[i].isEmpty()
								|| !tools[i].isCorrectToolForDrops(st))) {
							continue;
						}
						List<ItemStack> drops = Block.getDrops(st, level, at, null, p, tools[i]);
						if (drops.isEmpty()) {
							continue;
						}
						JsonObject one = new JsonObject();
						JsonArray d = new JsonArray();
						for (ItemStack s : drops) {
							d.add(id(s.getItem()));
						}
						one.add("drops", d);
						one.addProperty("tool", TOOLS[i]);
						all.add(String.valueOf(BuiltInRegistries.BLOCK.getKey(b)), one);
						break;
					} catch (Exception e) {
						break;                      // (a block that needs its block entity to drop: skip it)
					}
				}
			}
			o.add("sources", all);
			return o;
		}).get(10, TimeUnit.SECONDS);
	}

	/** The open container screen, if any (for clicking its slots): null when none is open. */
	static JsonObject screen(Minecraft mc) {
		Screen s = mc.gui.screen();
		if (!(s instanceof AbstractContainerScreen<?> cs)) {
			return null;
		}
		JsonObject o = new JsonObject();
		try {
			int left = (Integer) field(cs, "leftPos");
			int top = (Integer) field(cs, "topPos");
			int scale = mc.getWindow().getGuiScale();
			AbstractContainerMenu menu = cs.getMenu();
			o.addProperty("menu", menu.getClass().getSimpleName());
			JsonArray slots = new JsonArray();
			int grid = 0;
			for (Slot sl : menu.slots) {
				JsonArray one = new JsonArray();
				one.add(sl.index);
				one.add((left + sl.x + 8) * scale);           // the slot's middle, framebuffer pixels
				one.add((top + sl.y + 8) * scale);
				ItemStack st = sl.getItem();
				one.add(st.isEmpty() ? "" : id(st.getItem()));
				one.add(st.getCount());
				String role;
				if (menu instanceof AbstractFurnaceMenu && sl.index < 3) {
					role = sl.index == 0 ? "input" : sl.index == 1 ? "fuel" : "result";   // (the furnace's own)
				} else if (sl instanceof ResultSlot) {
					role = "result";
				} else if (sl.container instanceof CraftingContainer) {
					role = "grid" + grid++;
				} else if (sl.container instanceof Inventory) {
					int c = sl.getContainerSlot();
					role = c < 9 ? "hotbar" + c : c < 36 ? "main" + c : "other";
				} else {
					role = "other";
				}
				one.add(role);
				slots.add(one);
			}
			o.add("slots", slots);
			if (menu instanceof AbstractFurnaceMenu fm) {
				o.addProperty("cooking", fm.getBurnProgress());   // 0..1 through the one cooking now
				o.addProperty("lit", fm.isLit());
			}
			// where the pointer is (framebuffer pixels), and the slot under it as the game sees it: clicks are
			// checked against these before they're made (a click outside the window throws the held stack away)
			Object hovered = field(cs, "hoveredSlot");
			o.addProperty("hovered", hovered instanceof Slot hs ? hs.index : -1);
			o.add("mouse", Bridge.vec(mc.mouseHandler.getScaledXPos(mc.getWindow()) * scale,
					mc.mouseHandler.getScaledYPos(mc.getWindow()) * scale, 0));
			int iw = (Integer) field(cs, "imageWidth");
			int ih = (Integer) field(cs, "imageHeight");
			o.add("gui", Bridge.vec(left * scale, top * scale, (left + iw) * scale));
			o.addProperty("gui_bottom", (top + ih) * scale);
			ItemStack carried = menu.getCarried();
			o.addProperty("carried", carried.isEmpty() ? "" : id(carried.getItem()));
			o.addProperty("carried_count", carried.getCount());
		} catch (Exception e) {
			o.addProperty("error", String.valueOf(e));
		}
		return o;
	}
}
