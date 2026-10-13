package gabridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.mojang.datafixers.util.Pair;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderSet;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.tags.TagKey;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.levelgen.structure.Structure;

import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.chunk.status.ChunkStatus;
import net.minecraft.world.level.levelgen.structure.BoundingBox;
import net.minecraft.world.level.levelgen.structure.StructureStart;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

/**
 * Finding places as the game's own /locate does, on the single-player server's thread (read-only):
 *   {"op":"names"}                                         the structure, structure tag and biome ids
 *   {"op":"locate","kind":"structure","id":"minecraft:village"}   kind: structure (a structure or a tag of them)
 *   {"op":"locate","kind":"biome","id":"minecraft:cherry_grove"}  or biome
 * Answers {"found":true,"pos":[x,y,z],"id":...} or {"found":false}.
 *   {"op":"structure_box","pos":[x,y,z]}   the whole area of the structure that starts in that chunk (a
 *   village is a wide area, not a point): {"found":true,"min":[x,y,z],"max":[x,y,z]}, once its chunk is loaded
 *   (else {"found":false}: nothing is loaded or generated for this).
 */
final class Places {
	/** How far a structure search looks, in chunks (/locate's own is 100). */
	static final int STRUCTURE_RADIUS = 100;
	/** How far a biome search looks, in blocks, and its steps (as /locate biome). */
	static final int BIOME_RADIUS = 6400;

	private Places() {
	}

	static MinecraftServer server() {
		Minecraft mc = Minecraft.getInstance();
		if (!Bridge.singleplayer(mc)) {
			throw new IllegalStateException("not in a single-player world");
		}
		return mc.getSingleplayerServer();
	}

	static JsonObject names() throws Exception {
		MinecraftServer server = server();
		return server.submit(() -> {
			JsonObject o = new JsonObject();
			Registry<Structure> structures = server.registryAccess().lookupOrThrow(Registries.STRUCTURE);
			Registry<Biome> biomes = server.registryAccess().lookupOrThrow(Registries.BIOME);
			JsonArray s = new JsonArray();
			structures.keySet().forEach(k -> s.add(k.toString()));
			JsonArray t = new JsonArray();
			structures.getTags().forEach(tag -> t.add(tag.key().location().toString()));
			JsonArray b = new JsonArray();
			biomes.keySet().forEach(k -> b.add(k.toString()));
			o.add("structures", s);
			o.add("structure_tags", t);
			o.add("biomes", b);
			return o;
		}).get(5, TimeUnit.SECONDS);
	}

	static JsonObject box(int x, int y, int z) throws Exception {
		MinecraftServer server = server();
		return server.submit(() -> {
			JsonObject o = new JsonObject();
			List<ServerPlayer> players = server.getPlayerList().getPlayers();
			o.addProperty("found", false);
			if (players.isEmpty()) {
				return o;
			}
			ServerLevel level = players.get(0).level();
			ChunkAccess chunk = level.getChunk(x >> 4, z >> 4, ChunkStatus.STRUCTURE_STARTS, false);
			if (chunk == null) {
				return o;
			}
			BoundingBox best = null;
			for (StructureStart st : chunk.getAllStarts().values()) {
				if (st == null || !st.isValid()) {
					continue;
				}
				BoundingBox b = st.getBoundingBox();
				if (best == null || (b.minX() <= x && x <= b.maxX() && b.minZ() <= z && z <= b.maxZ())) {
					best = b;
				}
			}
			if (best != null) {
				o.addProperty("found", true);
				o.add("min", Bridge.vec(best.minX(), best.minY(), best.minZ()));
				o.add("max", Bridge.vec(best.maxX() + 1, best.maxY() + 1, best.maxZ() + 1));
			}
			return o;
		}).get(5, TimeUnit.SECONDS);
	}

	static JsonObject locate(String kind, String id) throws Exception {
		MinecraftServer server = server();
		return server.submit(() -> {
			JsonObject o = new JsonObject();
			List<ServerPlayer> players = server.getPlayerList().getPlayers();
			if (players.isEmpty()) {
				o.addProperty("found", false);
				o.addProperty("error", "no player");
				return o;
			}
			ServerPlayer p = players.get(0);
			ServerLevel level = p.level();
			BlockPos from = p.blockPosition();
			Identifier key = Identifier.tryParse(id);
			if (key == null) {
				o.addProperty("found", false);
				o.addProperty("error", "not an id: " + id);
				return o;
			}
			BlockPos at = null;
			if ("biome".equals(kind)) {
				Pair<BlockPos, Holder<Biome>> got = level.findClosestBiome3d(h -> h.is(key), from, BIOME_RADIUS, 32, 64);
				at = got == null ? null : got.getFirst();
			} else {
				Registry<Structure> structures = server.registryAccess().lookupOrThrow(Registries.STRUCTURE);
				TagKey<Structure> tag = TagKey.create(Registries.STRUCTURE, key);
				boolean isTag = structures.getTags().anyMatch(t -> t.key().equals(tag));
				if (isTag) {
					at = level.findNearestMapStructure(tag, from, STRUCTURE_RADIUS, false);
				} else {
					Optional<Holder.Reference<Structure>> one = structures.get(key);
					if (one.isPresent()) {
						at = level.findNearestMapStructure(HolderSet.direct(one.get()), from, STRUCTURE_RADIUS, false);
					} else {
						o.addProperty("error", "no such structure: " + id);
					}
				}
			}
			o.addProperty("found", at != null);
			o.addProperty("id", id);
			o.addProperty("dimension", String.valueOf(level.dimension().identifier()));
			if (at != null) {
				o.add("pos", Bridge.vec(at.getX(), at.getY(), at.getZ()));
			}
			return o;
		}).get(20, TimeUnit.SECONDS);
	}
}
