package gabridge;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.api.ClientModInitializer;
import net.minecraft.client.Minecraft;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;

/**
 * The Game Assistant's bridge into Minecraft (client side, single-player only).
 *
 * The assistant connects to 127.0.0.1:PORT and sends one JSON request per line; each gets one JSON line back:
 *   {"op":"snapshot"}                                    the latest state (made on the game's thread each frame)
 *   {"op":"raycast","rays":[[x1,y1,z1,x2,y2,z2],...]}   rays through the blocks (done at the next frame)
 *   {"op":"names"}, {"op":"locate",...}                  places, as /locate finds them (Places)
 *   {"op":"grid","min":[x,y,z],"size":[sx,sy,sz]}       what each block in a box is, for walking (Grid)
 *   {"op":"ping"}
 * Read-only: it never changes the world, and it stays off on multiplayer servers (offline single-player only).
 */
public class Bridge implements ClientModInitializer {
	public static final int PORT = 47821;
	/** Bumped with each change the assistant relies on (it says when the game runs an older bridge). */
	public static final int VERSION = 17;
	static final Gson GSON = new Gson();

	/** The latest snapshot as a JSON line, replaced every frame while the assistant asks for it. */
	static volatile String snapshot = "{\"v\":1,\"in_world\":false}";
	/** When the assistant last asked (no snapshots are made a few seconds after its last question). */
	static volatile long lastAsked = 0;
	/** Work for the game's thread (raycasts), run at the end of the next frame. */
	static final ConcurrentLinkedQueue<Runnable> work = new ConcurrentLinkedQueue<>();
	static long frame = 0;

	@Override
	public void onInitializeClient() {
		Thread t = new Thread(Bridge::serve, "Game Assistant bridge");
		t.setDaemon(true);
		t.start();
	}

	/** Every frame, on the game's thread (MinecraftMixin, at the end of Minecraft.runTick). */
	public static void onFrame(Minecraft mc) {
		frame++;
		Runnable job;
		while ((job = work.poll()) != null) {
			job.run();
		}
		if (System.currentTimeMillis() - lastAsked > 3000) {
			return;
		}
		try {
			snapshot = GSON.toJson(Snapshots.make(mc, frame));
		} catch (RuntimeException e) {
			JsonObject o = new JsonObject();
			o.addProperty("v", 1);
			o.addProperty("frame", frame);
			o.addProperty("in_world", false);
			o.addProperty("error", e.toString());
			snapshot = GSON.toJson(o);
		}
	}

	/** Run f on the game's thread at the next frame and wait for its answer (at most a second). */
	static JsonElement onGameThread(Function<Minecraft, JsonElement> f) throws Exception {
		CompletableFuture<JsonElement> done = new CompletableFuture<>();
		work.add(() -> {
			try {
				done.complete(f.apply(Minecraft.getInstance()));
			} catch (RuntimeException e) {
				done.completeExceptionally(e);
			}
		});
		return done.get(1, TimeUnit.SECONDS);
	}

	static void serve() {
		try (ServerSocket server = new ServerSocket(PORT, 4, InetAddress.getLoopbackAddress())) {
			while (true) {
				Socket s = server.accept();
				Thread t = new Thread(() -> talk(s), "Game Assistant bridge client");
				t.setDaemon(true);
				t.start();
			}
		} catch (Exception e) {
			System.err.println("[gabridge] the bridge could not start on port " + PORT + ": " + e);
		}
	}

	static void talk(Socket s) {
		try (s;
			 BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
			 Writer out = new OutputStreamWriter(s.getOutputStream(), StandardCharsets.UTF_8)) {
			s.setTcpNoDelay(true);
			String line;
			while ((line = in.readLine()) != null) {
				lastAsked = System.currentTimeMillis();
				String reply;
				try {
					reply = answer(JsonParser.parseString(line).getAsJsonObject());
				} catch (Exception e) {
					JsonObject err = new JsonObject();
					err.addProperty("error", e.toString());
					reply = GSON.toJson(err);
				}
				out.write(reply);
				out.write('\n');
				out.flush();
			}
		} catch (Exception e) {
			// the assistant went away
		}
	}

	static String answer(JsonObject req) throws Exception {
		String op = req.has("op") ? req.get("op").getAsString() : "";
		switch (op) {
			case "snapshot":
				return snapshot;
			case "raycast": {
				JsonArray rays = req.getAsJsonArray("rays");
				boolean fluids = req.has("fluids") && req.get("fluids").getAsBoolean();
				boolean outline = req.has("outline") && req.get("outline").getAsBoolean();
				return GSON.toJson(onGameThread(mc -> Rays.cast(mc, rays, fluids, outline)));
			}
			case "grid": {
				JsonArray min = req.getAsJsonArray("min");
				JsonArray size = req.getAsJsonArray("size");
				return GSON.toJson(onGameThread(mc -> Grid.read(mc, min, size)));
			}
			case "names":
				return GSON.toJson(Places.names());
			case "structure_box": {
				JsonArray p = req.getAsJsonArray("pos");
				return GSON.toJson(Places.box(p.get(0).getAsInt(), p.get(1).getAsInt(), p.get(2).getAsInt()));
			}
			case "recipes":
				return GSON.toJson(Crafting.recipes());
			case "sources":
				return GSON.toJson(Crafting.sources());
			case "find_entities": {
				int max = req.has("max") ? req.get("max").getAsInt() : 5;
				return GSON.toJson(Places.findEntities(req.getAsJsonArray("types"), max));
			}
			case "entity_types":
				return GSON.toJson(Places.entityTypes());
			case "dig_estimate": {
				JsonArray from = req.getAsJsonArray("from");
				JsonArray to = req.getAsJsonArray("to");
				return GSON.toJson(onGameThread(mc -> Grid.digEstimate(mc, from, to)));
			}
			case "heights": {
				JsonArray min = req.getAsJsonArray("min");
				JsonArray size = req.getAsJsonArray("size");
				boolean ground = req.has("ground") && req.get("ground").getAsBoolean();
				return GSON.toJson(onGameThread(mc -> Grid.heights(mc, min, size, ground)));
			}
			case "block_ids":
				return GSON.toJson(Finder.ids());
			case "find_blocks": {
				JsonArray ids = req.getAsJsonArray("ids");
				int max = req.has("max") ? req.get("max").getAsInt() : 8;
				int radius = req.has("radius") ? req.get("radius").getAsInt() : 0;
				return GSON.toJson(onGameThread(mc -> Finder.find(mc, ids, max, radius)));
			}
			case "locate":
				return GSON.toJson(Places.locate(req.has("kind") ? req.get("kind").getAsString() : "structure",
						req.get("id").getAsString()));
			case "ping": {
				JsonObject o = new JsonObject();
				o.addProperty("pong", true);
				o.addProperty("version", VERSION);
				o.addProperty("frame", frame);
				return GSON.toJson(o);
			}
			default: {
				JsonObject o = new JsonObject();
				o.addProperty("error", "unknown op: " + op);
				return GSON.toJson(o);
			}
		}
	}

	static JsonArray vec(double x, double y, double z) {
		JsonArray a = new JsonArray();
		a.add(x);
		a.add(y);
		a.add(z);
		return a;
	}

	/** Offline single-player only: a world of this computer's own, not a server (the project's rule). */
	static boolean singleplayer(Minecraft mc) {
		return mc.hasSingleplayerServer() && !mc.isMultiplayerServer();
	}
}
