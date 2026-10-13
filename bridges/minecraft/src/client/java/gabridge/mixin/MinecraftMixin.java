package gabridge.mixin;

import gabridge.Bridge;
import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** The bridge's frame hook: the end of every frame, on the game's own thread. */
@Mixin(Minecraft.class)
public abstract class MinecraftMixin {
	@Inject(method = "runTick", at = @At("TAIL"))
	private void gabridge$frame(boolean advanceGameTime, CallbackInfo ci) {
		Bridge.onFrame((Minecraft) (Object) this);
	}
}
